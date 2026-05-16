"""history_writer —— 把每条变更请求的「规格 / 计划 / 结果」沉淀到磁盘。

Phase D 的目的：让后续 LLM/agent 会话（以及业务员/开发自己）能直接翻阅
「doskill 对 CR-X 做过什么」，不必再重读全仓库。这一轮只负责**写**历史；
反向喂给下一次 /init 让 AGENTS.md 包含「最近改动摘要」是下一阶段的事。

约束：
- 这不是审批闸门。pipeline 不应因 history 写入失败而中断。
- 所有内容**确定性模板**填充，不调用 LLM。
- 多次 retry 同一 CR 会幂等覆盖目录下文件。
- 文件 UTF-8，中文友好；result.md 30-100 行量级。

体量/分级规则（do NOT 持久化到 DB —— 写入时即时计算）：
A CR 是 large 当下列**客观信号**满足 ≥2 项：
  1. 澄清问题数 > 1
  2. locate 入口文件数 ≥ 3
  3. raw_request.request_text 长度 > 80
  4. brief 自由文本（原始需求 + 澄清问答）命中关键字
     ("large-change" / "重构" / "新功能" / "重做"，case-insensitive)
  5. 改动文件数 > 3（dev_runner 可报告时才计入；为 None 则忽略该项）

恰好命中 1 项 → medium；0 项 → small。

只有 large 才写 spec.md / plan.md；small/medium 仅写 result.md。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from orchestrator.adapters.types import LocateResult, RawRequest, RequestBrief
from orchestrator.multi_repo import discover_sub_repos

logger = logging.getLogger(__name__)


# 关键字 —— 命中即视为「large-change 倾向」。小写匹配。
_LARGE_INTENT_KEYWORDS: tuple[str, ...] = (
    "large-change",
    "重构",
    "新功能",
    "重做",
)

# 各阈值集中放，方便调参 + 测试直接 import 校验
_MULTI_CLARIFY_THRESHOLD = 1          # > 1 个澄清问题
_MULTI_ENTRY_FILES_THRESHOLD = 3      # ≥ 3 个入口文件
_LONG_REQUEST_TEXT_THRESHOLD = 80     # > 80 字符
_MULTI_FILE_CHANGE_THRESHOLD = 3      # > 3 个改动文件

# size 阈值
_LARGE_MIN_HITS = 2
_MEDIUM_MIN_HITS = 1


@dataclass(frozen=True)
class SizeClassification:
    """size 分类结果 + 命中明细 —— spec.md 按命中项分节，所以要带 reasons。"""

    size: str  # "small" | "medium" | "large"
    reasons: tuple[str, ...]  # 各命中项的人话描述（中文）


def classify_size(
    *,
    brief: RequestBrief,
    raw_request: RawRequest,
    locate_result: LocateResult,
    dev_runner_files_changed: list[str] | None,
) -> SizeClassification:
    """根据客观信号判定 CR 大小 + 收集命中理由。

    `dev_runner_files_changed=None` 时跳过第 5 项 —— 不让缺数据反而升级评估。
    """
    reasons: list[str] = []

    clarify_count = len(brief.clarifications or [])
    if clarify_count > _MULTI_CLARIFY_THRESHOLD:
        reasons.append(
            f"多次澄清：共 {clarify_count} 个澄清问题（>{_MULTI_CLARIFY_THRESHOLD}）"
        )

    entry_count = len(locate_result.entry_files or [])
    if entry_count >= _MULTI_ENTRY_FILES_THRESHOLD:
        reasons.append(
            f"多文件入口：locate 命中 {entry_count} 个入口文件"
            f"（≥{_MULTI_ENTRY_FILES_THRESHOLD}）"
        )

    req_len = len(raw_request.request_text or "")
    if req_len > _LONG_REQUEST_TEXT_THRESHOLD:
        reasons.append(
            f"长需求文本：{req_len} 字符（>{_LONG_REQUEST_TEXT_THRESHOLD}），疑似跨切面"
        )

    if _matches_large_intent(brief, raw_request):
        reasons.append(
            "意图关键字：自由文本命中 large-change/重构/新功能/重做"
        )

    if dev_runner_files_changed is not None:
        n = len(dev_runner_files_changed)
        if n > _MULTI_FILE_CHANGE_THRESHOLD:
            reasons.append(
                f"多文件改动：dev runner 改了 {n} 个文件"
                f"（>{_MULTI_FILE_CHANGE_THRESHOLD}）"
            )

    hits = len(reasons)
    if hits >= _LARGE_MIN_HITS:
        size = "large"
    elif hits >= _MEDIUM_MIN_HITS:
        size = "medium"
    else:
        size = "small"
    return SizeClassification(size=size, reasons=tuple(reasons))


def _matches_large_intent(brief: RequestBrief, raw_request: RawRequest) -> bool:
    """扫描所有自由文本（原始需求 + 每个澄清问答）找 large-change 关键字。"""
    parts: list[str] = [raw_request.request_text or "", brief.original_text or ""]
    for qa in brief.clarifications or []:
        if not isinstance(qa, dict):
            continue
        parts.append(str(qa.get("question") or ""))
        parts.append(str(qa.get("answer") or ""))
    haystack = " ".join(parts).lower()
    return any(kw in haystack for kw in _LARGE_INTENT_KEYWORDS)


def _history_root(repo_path: str) -> Path:
    """决定 .doskill/history/ 应落在哪个目录下。

    语义规则：
    - 多仓项目（repo_path 下有含 .git/ 的子目录）→ repo_path 本身是项目根，
      历史落在 <repo_path>/.doskill/history/（不在任一子仓内部）。
    - 单仓项目（无子目录含 .git/）→ 保持原有行为，
      历史同样落在 <repo_path>/.doskill/history/。

    无论单仓或多仓，路径计算结果相同；区别在语义上——多仓时
    repo_path 是包含 N 个子仓的项目根，单仓时 repo_path 就是 git 根本身。

    Args:
        repo_path: 项目路径（可以是单仓 git 根，也可以是多仓项目顶层目录）。

    Returns:
        Path to .doskill/history/ directory (not yet created).
    """
    project_path = Path(repo_path)
    # discover_sub_repos 扫顶层子目录：多仓时非空，单仓时为空。
    # 无论哪种情况，history 都挂在 project_path 下。
    _ = discover_sub_repos(project_path)  # 触发多仓语义检测（结果不影响路径）
    return project_path / ".doskill" / "history"


def write_history(
    *,
    repo_path: str,
    request_id: str,
    raw_request: RawRequest,
    brief: RequestBrief,
    locate_result: LocateResult,
    branch: str,
    preview_url: str | None,
    phase_timings: dict[str, float],
    total_elapsed: float,
    dev_runner_files_changed: list[str] | None,
) -> None:
    """把变更请求的历史落到 <repo>/.doskill/history/cr-<id>/。

    - 总是写 result.md（小/中/大 通用）。
    - 仅 large 写 spec.md + plan.md。
    - 同 CR 重跑 → 同一目录覆盖（幂等）。
    - 写入失败由调用方捕获记录，本函数不吞异常。
    - 多仓项目（repo_path 含子仓）时，历史落在项目根，不在任一子仓内部。
    """
    classification = classify_size(
        brief=brief,
        raw_request=raw_request,
        locate_result=locate_result,
        dev_runner_files_changed=dev_runner_files_changed,
    )

    out_dir = _history_root(repo_path) / f"cr-{request_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    result_md = _render_result(
        request_id=request_id,
        raw_request=raw_request,
        brief=brief,
        locate_result=locate_result,
        branch=branch,
        preview_url=preview_url,
        phase_timings=phase_timings,
        total_elapsed=total_elapsed,
        dev_runner_files_changed=dev_runner_files_changed,
        classification=classification,
    )
    _atomic_write(out_dir / "result.md", result_md)

    if classification.size == "large":
        spec_md = _render_spec(
            request_id=request_id,
            raw_request=raw_request,
            brief=brief,
            classification=classification,
        )
        _atomic_write(out_dir / "spec.md", spec_md)

        plan_md = _render_plan(
            request_id=request_id,
            phase_timings=phase_timings,
            locate_result=locate_result,
            branch=branch,
            preview_url=preview_url,
        )
        _atomic_write(out_dir / "plan.md", plan_md)
    else:
        # 之前是 large、retry 后降为 small/medium → 清掉旧 spec/plan，避免误导
        for stale in ("spec.md", "plan.md"):
            stale_path = out_dir / stale
            if stale_path.exists():
                try:
                    stale_path.unlink()
                except OSError:
                    logger.warning("无法删除遗留 %s", stale_path)


# ── 渲染层 ─────────────────────────────────────────────────────────────


def _render_result(
    *,
    request_id: str,
    raw_request: RawRequest,
    brief: RequestBrief,
    locate_result: LocateResult,
    branch: str,
    preview_url: str | None,
    phase_timings: dict[str, float],
    total_elapsed: float,
    dev_runner_files_changed: list[str] | None,
    classification: SizeClassification,
) -> str:
    lines: list[str] = []
    lines.append(f"# CR {request_id} —— 变更结果")
    lines.append("")
    lines.append(f"- 生成时间：{_now_iso()}")
    lines.append(f"- 体量分级：**{classification.size}**")
    lines.append(f"- 总耗时：{total_elapsed:.1f}s")
    lines.append(f"- 分支：`{branch}`")
    lines.append(f"- 预览 URL：{preview_url or '（未生成）'}")
    lines.append("")

    lines.append("## 原始请求")
    lines.append("")
    lines.append(f"- URL：{raw_request.url}")
    lines.append(f"- 框选坐标：`{raw_request.box_coords}`")
    lines.append(f"- 视口：`{raw_request.viewport}`")
    lines.append("")
    lines.append("> " + _quote_block(raw_request.request_text or "(空)"))
    lines.append("")

    lines.append("## 澄清问答")
    lines.append("")
    if brief.clarifications:
        for i, qa in enumerate(brief.clarifications, 1):
            q = (qa.get("question") if isinstance(qa, dict) else None) or "(无问题)"
            a = (qa.get("answer") if isinstance(qa, dict) else None) or "(无回答)"
            lines.append(f"{i}. **Q**：{q}")
            lines.append(f"   **A**：{a}")
    else:
        lines.append("（无澄清问答 —— LLM 直接判定需求清晰）")
    if brief.selected_mockup is not None:
        lines.append("")
        lines.append(
            f"- 选定 HTML 方案：`{brief.selected_mockup.id}` / "
            f"{brief.selected_mockup.title}"
        )
    lines.append("")

    lines.append("## 定位")
    lines.append("")
    lines.append(f"- 路由路径：`{locate_result.route_path or '(空)'}`")
    if locate_result.entry_files:
        lines.append("- 入口文件：")
        for f in locate_result.entry_files:
            lines.append(f"  - `{f}`")
    else:
        lines.append("- 入口文件:（无）")
    lines.append("")

    lines.append("## 改动文件")
    lines.append("")
    if dev_runner_files_changed is None:
        lines.append("（dev runner 未报告改动列表）")
    elif not dev_runner_files_changed:
        lines.append("（无改动 —— 异常情况）")
    else:
        for f in dev_runner_files_changed:
            lines.append(f"- `{f}`")
    lines.append("")

    lines.append("## 各阶段耗时")
    lines.append("")
    if phase_timings:
        for phase, elapsed in phase_timings.items():
            lines.append(f"- {phase}：{elapsed:.1f}s")
    else:
        lines.append("（无耗时数据）")
    lines.append("")

    if classification.reasons:
        lines.append("## 分级命中项")
        lines.append("")
        for r in classification.reasons:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _render_spec(
    *,
    request_id: str,
    raw_request: RawRequest,
    brief: RequestBrief,
    classification: SizeClassification,
) -> str:
    lines: list[str] = []
    lines.append(f"# CR {request_id} —— Spec（large 变更）")
    lines.append("")
    lines.append(f"- 生成时间：{_now_iso()}")
    lines.append(f"- 体量：**{classification.size}**")
    lines.append("")

    lines.append("## 原始需求")
    lines.append("")
    lines.append("> " + _quote_block(raw_request.request_text or "(空)"))
    lines.append("")
    lines.append(f"- 页面 URL：{raw_request.url}")
    lines.append("")

    lines.append("## 澄清后的意图")
    lines.append("")
    if brief.clarifications:
        for i, qa in enumerate(brief.clarifications, 1):
            q = (qa.get("question") if isinstance(qa, dict) else None) or "(无)"
            a = (qa.get("answer") if isinstance(qa, dict) else None) or "(无)"
            lines.append(f"{i}. {q} → {a}")
    else:
        lines.append("（无补充问答）")
    if brief.selected_mockup is not None:
        lines.append("")
        lines.append(
            f"- 选定方案：`{brief.selected_mockup.id}` —— "
            f"{brief.selected_mockup.title}"
        )
    lines.append("")

    lines.append("## 触发分级的客观信号")
    lines.append("")
    if classification.reasons:
        for r in classification.reasons:
            lines.append(f"### {r}")
            lines.append("")
            lines.append(_explain_reason(r, brief=brief, raw_request=raw_request))
            lines.append("")
    else:
        lines.append("（无 —— 不应到达此处）")
        lines.append("")

    return "\n".join(lines) + "\n"


def _render_plan(
    *,
    request_id: str,
    phase_timings: dict[str, float],
    locate_result: LocateResult,
    branch: str,
    preview_url: str | None,
) -> str:
    lines: list[str] = []
    lines.append(f"# CR {request_id} —— Plan（large 变更）")
    lines.append("")
    lines.append(f"- 生成时间：{_now_iso()}")
    lines.append(f"- 分支：`{branch}`")
    lines.append(f"- 预览 URL：{preview_url or '(未生成)'}")
    lines.append("")
    lines.append("> Pipeline 实际执行的阶段（按发生顺序）。每节附该阶段产出/结论。")
    lines.append("")

    # 按已知顺序展示；保留可能的未知 phase 顺尾
    known_order = ["clarifying", "locating", "coding", "building"]
    seen: set[str] = set()
    for phase in known_order:
        if phase in phase_timings:
            seen.add(phase)
            lines.extend(
                _plan_phase_block(
                    phase=phase,
                    elapsed=phase_timings[phase],
                    locate_result=locate_result,
                    branch=branch,
                    preview_url=preview_url,
                )
            )
    for phase, elapsed in phase_timings.items():
        if phase in seen:
            continue
        lines.extend(
            _plan_phase_block(
                phase=phase,
                elapsed=elapsed,
                locate_result=locate_result,
                branch=branch,
                preview_url=preview_url,
            )
        )

    return "\n".join(lines) + "\n"


def _plan_phase_block(
    *,
    phase: str,
    elapsed: float,
    locate_result: LocateResult,
    branch: str,
    preview_url: str | None,
) -> list[str]:
    outcome = _phase_outcome(phase, locate_result, branch, preview_url)
    return [
        f"## {phase}",
        "",
        f"- 耗时：{elapsed:.1f}s",
        f"- 产出：{outcome}",
        "",
    ]


def _phase_outcome(
    phase: str,
    locate_result: LocateResult,
    branch: str,
    preview_url: str | None,
) -> str:
    if phase == "clarifying":
        return "完成业务澄清，产出 RequestBrief"
    if phase == "locating":
        ef = ", ".join(locate_result.entry_files[:3]) or "(无)"
        return f"定位入口文件：{ef}"
    if phase == "coding":
        return f"在分支 `{branch}` 上完成代码改动并 commit"
    if phase == "building":
        return f"构建 + 拉起预览容器，URL：{preview_url or '(未生成)'}"
    return "（无标准化描述）"


def _explain_reason(
    reason: str, *, brief: RequestBrief, raw_request: RawRequest
) -> str:
    """给 spec.md 每个命中项一段人话补充。"""
    if reason.startswith("多次澄清"):
        bullets = []
        for qa in brief.clarifications or []:
            q = (qa.get("question") if isinstance(qa, dict) else None) or "(无)"
            a = (qa.get("answer") if isinstance(qa, dict) else None) or "(无)"
            bullets.append(f"- {q} → {a}")
        return "\n".join(bullets) if bullets else "（无问答 —— 不应到达此处）"
    if reason.startswith("多文件入口"):
        return "意味着改动横跨多个组件/路由，需要更谨慎地核对一致性。"
    if reason.startswith("长需求文本"):
        return (
            "原始需求长度提示跨切面诉求，原文：\n\n> "
            + _quote_block(raw_request.request_text or "")
        )
    if reason.startswith("意图关键字"):
        return (
            "原始描述或澄清问答里出现 重构/新功能/重做/large-change，"
            "建议按 large 流程留痕。"
        )
    if reason.startswith("多文件改动"):
        return "dev runner 实际改动的文件数超过阈值，提示改动面较广。"
    return "（无补充说明）"


# ── 工具 ───────────────────────────────────────────────────────────────


def _atomic_write(path: Path, text: str) -> None:
    """先写 .tmp 再原子 rename —— 防止读者读到半截 markdown。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote_block(text: str) -> str:
    """把多行文本压成 markdown blockquote 友好的一行；保留换行用 '\\n> ' 续行。"""
    text = (text or "").strip()
    if not text:
        return "(空)"
    return text.replace("\n", "\n> ")
