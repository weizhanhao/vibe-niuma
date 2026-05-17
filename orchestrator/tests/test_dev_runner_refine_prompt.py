"""Plan 10 Task 6: dev_runner build_prompt 按 ctx.mode 分支。

业务员说「字号大一点」走 refine_cr → DevContext.mode='refine' + base_branch
设置。build_prompt 要给 dev runner（opencode/claude-code）告知「续改 base
branch、看之前的 diff、按 chat history 调整」，而不是「新建一个改动」。
"""
from __future__ import annotations

from orchestrator.adapters.impl.claude_code_runner import build_prompt
from orchestrator.adapters.types import (
    DevContext, LocateResult, RawRequest, RequestBrief,
)


def _ctx(*, mode: str = "new", base_branch: str | None = None,
         request_text: str = "改", chat_history: list | None = None,
         entry_files: list[str] | None = None) -> DevContext:
    brief = RequestBrief(
        original_text=request_text,
        clarifications=[],
        selected_mockup=None,
    )
    locate = LocateResult(
        entry_files=entry_files or ["src/pages/Orders.tsx"],
        route_path="/orders",
    )
    ctx = DevContext(
        brief=brief,
        locate_result=locate,
        screenshot_b64="img",
        box_coords={},
        entry_file_contents={},
        chat_history=chat_history or [],
        mode=mode,
        base_branch=base_branch,
    )
    return ctx


# ── new_cr 路径（现状不破）─────────────────────────────────────────


def test_new_cr_prompt_does_not_mention_refine_or_base_branch():
    p = build_prompt(_ctx(mode="new", request_text="把订单徽章改红"))
    assert "续改" not in p
    assert "base branch" not in p.lower()
    assert "已经在分支" not in p
    assert "把订单徽章改红" in p


def test_new_cr_prompt_keeps_brief_text():
    p = build_prompt(_ctx(mode="new", request_text="加搜索按钮"))
    assert "加搜索按钮" in p


# ── refine_cr 路径（新增）──────────────────────────────────────────


def test_refine_prompt_mentions_continue_on_base_branch():
    p = build_prompt(_ctx(
        mode="refine",
        base_branch="cr/abc123",
        request_text="字号大一点",
    ))
    assert "cr/abc123" in p
    assert "续改" in p or "已经在分支" in p or "continue" in p.lower()


def test_refine_prompt_includes_user_followup_message():
    p = build_prompt(_ctx(
        mode="refine",
        base_branch="cr/abc",
        request_text="字号大一点，颜色再淡点",
    ))
    assert "字号大一点" in p
    assert "颜色再淡点" in p


def test_refine_prompt_includes_chat_history():
    p = build_prompt(_ctx(
        mode="refine",
        base_branch="cr/abc",
        request_text="字号大一点",
        chat_history=[
            {"type": "user", "ts": "t0", "content": "改红"},
            {"type": "ai", "ts": "t1", "content": "改完了"},
        ],
    ))
    assert "改红" in p
    assert "改完了" in p


def test_refine_prompt_does_not_dump_full_entry_files_again():
    """refine 时业务员已经在 base branch 上改过；不需要 prompt 再 dump 全
    entry_file_contents（节省 token）。runner 自己用 git diff 看差异。"""
    big_content = "very long file " * 1000
    ctx = _ctx(mode="refine", base_branch="cr/abc", request_text="再大点")
    ctx.entry_file_contents = {"src/Orders.tsx": big_content}
    p = build_prompt(ctx)
    assert big_content not in p
    assert "git diff" in p or "diff" in p.lower() or "上一轮" in p


def test_refine_prompt_still_lists_entry_file_paths():
    """文件路径还在（让 runner 知道改哪儿），但不 dump 内容。"""
    ctx = _ctx(
        mode="refine",
        base_branch="cr/abc",
        request_text="改",
        entry_files=["src/components/OrderBadge.tsx", "src/pages/Orders.tsx"],
    )
    p = build_prompt(ctx)
    assert "src/components/OrderBadge.tsx" in p
    assert "src/pages/Orders.tsx" in p


def test_refine_prompt_tells_runner_to_commit_to_existing_branch():
    """关键：runner 要在 base_branch 上 git commit，不能新建 branch。"""
    p = build_prompt(_ctx(
        mode="refine",
        base_branch="cr/abc123",
        request_text="改",
    ))
    assert "当前分支" in p or "已存在" in p or "git commit" in p
