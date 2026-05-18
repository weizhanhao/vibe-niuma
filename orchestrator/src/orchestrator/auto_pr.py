"""auto_pr —— 维护业务员合并目标分支 → main 的 long-running PR。

Plan 11 · M1.T7.

设计：
- 业务员每合一条 CR，pipeline 在 push 完 target_branch 之后调 maintain_pr。
- maintain_pr 用 GitHubAPI.upsert_pr：远端找已开放 PR 就 update body，没有就 create。
- PR body 用 build_pr_body 拼接，**滚动窗口** —— 只列最近 N 条 CR 详情，
  超过窗口的折叠成单行 summary（防 body 超过 GitHub 65k 限制 + 程序员能扫读）。
- title 固定 "[vibe-niuma] 业务员合并请求 (N 条改动)"，N = 总 CR 数。

每条 CR 在 body 里渲染成：
    ### #cr-abc123 · 2026-05-18 14:20
    业务员说："订单徽章改红色"
    commit: a1b2c3d
    [📷 截图](https://...)

老于窗口的折叠成：
    <details><summary>更早 5 条已折叠</summary>
    - #cr-old1 · 2026-05-17 · "字号大一点"
    - ...
    </details>
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from orchestrator.github_client import GitHubAPI, NotFoundError

logger = logging.getLogger(__name__)

# 默认滚动窗口：最近 20 条 CR 渲染完整描述，更老的折叠
DEFAULT_PR_BODY_WINDOW = 20

# GitHub PR body 硬限制是 65536 字符；保守留 60k 给我们用
MAX_PR_BODY_CHARS = 60_000


@dataclass(frozen=True)
class CRRecord:
    """一条 CR 在 PR body 里要展示的关键信息。"""
    cr_id: str
    business_text: str           # 业务员原话（澄清前最初的需求文字）
    commit_sha: str              # CR merge 到 target_branch 时的 commit sha
    timestamp_iso: str           # ISO 8601 时间戳
    screenshot_url: Optional[str] = None  # 业务员截图的可访问 URL（可选）


def _truncate(text: str, max_chars: int = 500) -> str:
    """单条 CR 业务文本截断 —— 避免业务员粘超长 prompt 撑爆 PR body。"""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _render_recent_block(record: CRRecord) -> str:
    """单条 CR 渲染成 markdown 块。"""
    lines = [
        f"### #{record.cr_id} · {record.timestamp_iso}",
        f"> {_truncate(record.business_text)}",
        f"commit: `{record.commit_sha[:10]}`",
    ]
    if record.screenshot_url:
        lines.append(f"[📷 截图]({record.screenshot_url})")
    return "\n".join(lines)


def _render_folded_block(records: list[CRRecord]) -> str:
    """老于窗口的 CRs 折叠成一段 <details>。"""
    if not records:
        return ""
    items = []
    for r in records:
        items.append(
            f"- #{r.cr_id} · {r.timestamp_iso} · {_truncate(r.business_text, 80)!r}"
        )
    return (
        f"<details><summary>更早 {len(records)} 条已折叠</summary>\n\n"
        + "\n".join(items)
        + "\n\n</details>"
    )


def build_pr_body(
    records: list[CRRecord],
    *,
    target_branch: str,
    base_branch: str,
    window: int = DEFAULT_PR_BODY_WINDOW,
) -> str:
    """渲染整个 PR body。

    records 按时间从老到新排序（最新的在末尾）。
    渲染顺序：最新的在最上面（程序员一打开 PR 看到的就是最近的改动）。
    """
    if not records:
        body = (
            f"**业务员还没合并过 CR。**\n\n"
            f"`{target_branch}` → `{base_branch}` PR 已就绪，等业务员开始改业务。"
        )
        return body

    # 排序：最新在前
    sorted_records = sorted(records, key=lambda r: r.timestamp_iso, reverse=True)
    recent = sorted_records[:window]
    folded = sorted_records[window:]

    header = (
        f"## 业务员合并请求 ({len(records)} 条)\n\n"
        f"`{target_branch}` → `{base_branch}` long-running PR，"
        f"业务员每合一条 CR 自动在这里追加描述。\n\n"
        f"程序员 review 后合到 `{base_branch}`。\n\n"
        f"---\n"
    )

    parts = [header]
    for r in recent:
        parts.append(_render_recent_block(r))
        parts.append("\n---\n")

    if folded:
        parts.append(_render_folded_block(folded))

    body = "\n".join(parts)

    # 硬限制保护：超过 60k 就再砍掉老的 recent
    while len(body) > MAX_PR_BODY_CHARS and len(recent) > 1:
        # 把最老的 recent 挪到 folded
        moved = recent.pop()
        folded.insert(0, moved)
        parts = [header]
        for r in recent:
            parts.append(_render_recent_block(r))
            parts.append("\n---\n")
        parts.append(_render_folded_block(folded))
        body = "\n".join(parts)

    return body


def build_pr_title(records: list[CRRecord]) -> str:
    """PR 标题：固定格式 + 当前条数。"""
    n = len(records)
    if n == 0:
        return "[vibe-niuma] 业务员合并请求"
    return f"[vibe-niuma] 业务员合并请求 ({n} 条改动)"


async def maintain_pr(
    api: GitHubAPI,
    *,
    owner: str,
    repo: str,
    target_branch: str,
    base_branch: str,
    records: list[CRRecord],
) -> tuple[dict, bool]:
    """upsert PR for target_branch → base_branch。

    returns: (pr_object, created_this_time)

    pr_object 是 GitHub PR 完整对象（含 number / html_url）。
    created_this_time = True 表示这次是新建的；False 表示更新了已有 PR。

    异常：透传 GitHubError 子类。调用方决定是否吞（pipeline 通常**不**让
    auto_pr 失败阻塞业务员的 merge —— PR 更新失败不致命）。
    """
    title = build_pr_title(records)
    body = build_pr_body(records, target_branch=target_branch, base_branch=base_branch)

    try:
        pr, created = await api.upsert_pr(
            owner,
            repo,
            head_branch=target_branch,
            base_branch=base_branch,
            title=title,
            body=body,
        )
    except NotFoundError:
        # 仓库 / PAT 看不到 → 给清晰错误，调用方决定提示业务员还是静默
        logger.warning(
            "maintain_pr: 仓库 %s/%s 找不到或 PAT 看不到", owner, repo
        )
        raise

    if created:
        logger.info(
            "maintain_pr: 新建 PR #%s for %s/%s (%s → %s)",
            pr.get("number"), owner, repo, target_branch, base_branch,
        )
    else:
        logger.info(
            "maintain_pr: 更新 PR #%s for %s/%s",
            pr.get("number"), owner, repo,
        )
    return pr, created
