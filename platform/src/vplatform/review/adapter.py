"""CodeReviewAdapter 接口（§9.5）。

包住 alibaba/open-code-review，保持可换 —— 同 D10 的接缝纪律。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# 缺陷轴（ocr）/ 规格轴（code-review skill）/ 规范轴（同 skill）
AXIS_DEFECT, AXIS_SPEC, AXIS_NORM = "defect", "spec", "norm"

# ocr 的四档
SEVERITIES = ("critical", "high", "medium", "low")


@dataclass
class Finding:
    axis: str
    severity: str
    category: str
    path: str
    start_line: int = 0
    end_line: int = 0
    claim: str = ""
    failure_scenario: str = ""
    existing_code: str = ""
    suggestion_code: str = ""
    # 自建过滤层填
    kept: bool = True
    verdict_reason: str = ""
    confidence: str = ""

    @property
    def anchor(self) -> str:
        return f"{self.path}:{self.start_line}"


@dataclass
class ReviewResult:
    findings: list[Finding] = field(default_factory=list)
    tokens: int = 0
    elapsed: str = ""
    files_reviewed: int = 0
    session_id: str = ""
    # **§9.7 ②：ocr 在 3/10 请求失败时仍返回 status=complete、退出码 0。**
    # 只看退出码等于蒙眼跑，所以这个字段必须一路传上去。
    failed_requests: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return self.failed_requests > 0


class ReviewNotInstalled(Exception):
    """复核工具没装。**跟「复核失败」是两回事** —— 不该让需求判失败。

    放在抽象层而不是 ocr 模块里：编排层要能捕获它，而编排层
    **不该 import 具体适配器**（接缝守卫会拦）。
    """


class ReviewError(RuntimeError):
    pass


@runtime_checkable
class CodeReviewAdapter(Protocol):
    async def review(self, *, repo_path: str, base: str, head: str,
                     background: str = "", rules_path: str | None = None,
                     token_budget: int = 200_000) -> ReviewResult: ...


def build_background(*, title: str, body: str, clarifications: list[dict] | None = None,
                     contracts: list[str] | None = None) -> str:
    """组装复核背景（§9.3）。

    放在 adapter 层而不是 ocr.py：这是**契约层的 prompt 组装**，
    换掉 ocr 实现它也照样成立。放错层会让编排层被迫 import 具体实现。

    这是质量杠杆：reviewer 知道这次改动**本来该做什么**，才能审
    「有没有做到需求」，而不只是审代码味道。
    """
    parts = [f"# 需求：{title}", "", body.strip(), ""]
    if clarifications:
        parts += ["## 澄清问答", ""]
        for c in clarifications:
            parts += [f"- Q: {c.get('question','')}", f"  A: {c.get('answer','')}"]
        parts.append("")
    if contracts:
        parts += ["## 接口契约（跨仓任务基于它并行开发，实现必须符合）", ""]
        parts += [f"- `{c}`" for c in contracts]
        parts.append("")
    parts += ["## 审查重点", "",
              "- 实现是否达成上述需求与契约；不符即为缺陷，不是风格问题",
              "- 只报本次 diff 触及的行"]
    return "\n".join(parts)
