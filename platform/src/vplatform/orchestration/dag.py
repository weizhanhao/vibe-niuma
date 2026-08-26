"""声明式 DAG 引擎（D8 + D12）。

v1 的流程是 `pipeline.py` 里 819 行硬编码 if-else。这里流程是配置：

    pipeline:
      - triage:    {skill: triage}
      - clarify:   {skill: grilling, gate: auto}
      - decompose: {skill: to-tickets, critic: decompose-critic, output: tasks[]}
      - implement: {parallel: tasks, skill: tdd, workspace: required}
      - verify:    {run: [lint, test, build],
                    on_failure: {skill: diagnosing-bugs, max_attempts: 2}}
      - ai_review: {adapter: ocr, block_on: [critical], plus_skill: code-review}
      - preview:   {expose: true, env: preview}
      - review:    {gate: human, approvers: 1}
      - merge:     {queue: per-repo, conflict: [git, mergiraf, {skill: resolving-merge-conflicts}]}
      - deploy_test: {adapter: deploy, env: test}
      - integrate: {run: [e2e], env: test}
      - release:   {gate: human, adapter: deploy, env: prod}

**加环节 = 改 YAML；换环节实现 = 换 skill 文件。** 都不动编排代码。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


# 内置环节的中文名。自定义环节在 YAML 里写 `label:` 即可。
_DEFAULT_LABELS: dict[str, str] = {
    "triage": "分诊",
    "clarify": "澄清",
    "decompose": "拆解",
    "implement": "并行开发",
    "verify": "验证",
    "ai_review": "AI 复核",
    "preview": "预览",
    "browser_check": "浏览器自检",
    "review": "人工审核",
    "merge": "合并",
    "deploy_test": "部署测试环境",
    "integrate": "集成测试",
    "release": "上线",
}


class PipelineError(ValueError):
    """流水线配置非法。**在加载期就炸**，不要等跑到那一步才发现。"""


@dataclass(frozen=True)
class Stage:
    key: str
    spec: dict = field(default_factory=dict)

    # ── 环节实现从哪来 ──────────────────────────────────────────
    @property
    def label(self) -> str:
        """给人看的名字。

        **key 不做成中文**：它是 DB 里的 `Requirement.stage`、幂等键
        `req:<id>:<key>` 的一部分、DISPATCH 的查找键。改成中文会让幂等键
        带上编码问题、也没法安全地重命名。名字归名字，标识符归标识符。

        YAML 里写 `label: 澄清` 覆盖；没写就退回 key。
        """
        return str(self.spec.get("label") or _DEFAULT_LABELS.get(self.key, self.key))

    @property
    def skill(self) -> str | None:
        return self.spec.get("skill")

    @property
    def critic(self) -> str | None:
        return self.spec.get("critic")

    @property
    def plus_skill(self) -> str | None:
        return self.spec.get("plus_skill")

    @property
    def adapter(self) -> str | None:
        return self.spec.get("adapter")

    # ── 调度语义 ───────────────────────────────────────────────
    @property
    def is_human_gate(self) -> bool:
        return self.spec.get("gate") == "human"

    @property
    def approvers(self) -> int:
        return int(self.spec.get("approvers", 1))

    @property
    def parallel_over(self) -> str | None:
        return self.spec.get("parallel")

    @property
    def needs_workspace(self) -> bool:
        return self.spec.get("workspace") == "required"

    @property
    def commands(self) -> list[str]:
        return list(self.spec.get("run") or [])

    @property
    def env(self) -> str | None:
        return self.spec.get("env")

    @property
    def block_on(self) -> tuple[str, ...]:
        return tuple(self.spec.get("block_on") or ())

    @property
    def on_failure(self) -> dict:
        return dict(self.spec.get("on_failure") or {})

    @property
    def conflict_chain(self) -> list:
        return list(self.spec.get("conflict") or [])


@dataclass
class Pipeline:
    stages: list[Stage]

    def __post_init__(self) -> None:
        keys = [s.key for s in self.stages]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            raise PipelineError(f"环节名重复：{sorted(dup)}")
        if not keys:
            raise PipelineError("流水线至少要有一个环节")

    def index(self, key: str) -> int:
        for i, s in enumerate(self.stages):
            if s.key == key:
                return i
        raise PipelineError(f"没有名为 {key!r} 的环节")

    def get(self, key: str) -> Stage:
        return self.stages[self.index(key)]

    def next_of(self, key: str) -> Stage | None:
        i = self.index(key)
        return self.stages[i + 1] if i + 1 < len(self.stages) else None

    def is_before(self, a: str, b: str) -> bool:
        return self.index(a) < self.index(b)

    @property
    def human_gates(self) -> list[Stage]:
        return [s for s in self.stages if s.is_human_gate]

    @property
    def required_skills(self) -> set[str]:
        """本流水线用到的全部 skill —— 部署前用它校验 skill 是否装齐。"""
        out: set[str] = set()
        for s in self.stages:
            for name in (s.skill, s.critic, s.plus_skill):
                if name:
                    out.add(name)
            for item in s.conflict_chain:
                if isinstance(item, dict) and item.get("skill"):
                    out.add(item["skill"])
            if s.on_failure.get("skill"):
                out.add(s.on_failure["skill"])
        return out


def load_pipeline(text: str) -> Pipeline:
    """解析 YAML。每个环节是 `{name: spec}` 的单键字典，顺序即执行顺序。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PipelineError(f"YAML 解析失败：{exc}") from exc

    if not isinstance(data, dict) or "pipeline" not in data:
        raise PipelineError("顶层必须是含 `pipeline:` 键的映射")
    raw = data["pipeline"]
    if not isinstance(raw, list):
        raise PipelineError("`pipeline:` 必须是列表")

    stages: list[Stage] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or len(item) != 1:
            raise PipelineError(f"第 {i + 1} 个环节必须是单键映射 `{{name: spec}}`，得到 {item!r}")
        (key, spec), = item.items()
        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            raise PipelineError(f"环节 {key!r} 的配置必须是映射，得到 {spec!r}")
        stages.append(Stage(key=str(key), spec=spec))
    return Pipeline(stages)


def load_pipeline_file(path: str | Path) -> Pipeline:
    return load_pipeline(Path(path).read_text(encoding="utf-8"))


DEFAULT_PIPELINE = """
pipeline:
  - triage:      {skill: triage}
  - clarify:     {skill: grilling, gate: auto}
  - decompose:   {skill: to-tickets, critic: decompose-critic, output: "tasks[]"}
  - implement:   {parallel: tasks, skill: tdd, workspace: required}
  - verify:      {run: [lint, test, build],
                  on_failure: {skill: diagnosing-bugs, max_attempts: 2}}
  - ai_review:   {adapter: ocr, block_on: [critical], plus_skill: code-review}
  - preview:     {expose: true, env: preview}
  - browser_check: {skill: ego-browser, env: preview}
  - review:      {gate: human, approvers: 1}
  - merge:       {queue: per-repo,
                  conflict: [git, mergiraf, {skill: resolving-merge-conflicts}]}
  - deploy_test: {adapter: deploy, env: test}
  - integrate:   {run: [e2e], env: test}
  - release:     {gate: human, adapter: deploy, env: prod}
"""


def default_pipeline() -> Pipeline:
    return load_pipeline(DEFAULT_PIPELINE)
