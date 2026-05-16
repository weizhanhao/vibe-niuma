"""Plan 9 Task 4: compaction —— 估 token + 老 AI 消息压成摘要。

设计要点（spec §Task 4）：
- estimate_tokens 用 tiktoken cl100k_base，±10% 即可（中英文混合时本身就不准）
- compact(messages, threshold_soft=5_000, threshold_hard=56_000) → 新 messages
  - < soft → 原样返回
  - >= soft 起跑压缩：所有 user 消息 / 最近 6 个 user-AI pair / 标 [PRESERVE]
    的 AI 消息保留；其他 AI 消息合并给 LLM 出一段 summary 插在被压窗口前
  - 老 messages 不删；调用方决定写不写
- LLM 调用通过依赖注入（CompactionLLM Protocol），测试可注入 fake
"""
from __future__ import annotations

import pytest

from orchestrator.compaction import (
    COMPACTION_PROMPT,
    compact,
    estimate_tokens,
)


def _msg(t: str, content: str, **extra) -> dict:
    return {"type": t, "ts": "2026-05-23T00:00:00", "content": content, **extra}


class _FakeLLM:
    """记录调用 prompt 的伪 LLM；返回固定摘要文本。"""

    def __init__(self, summary: str = "（摘要）"):
        self.summary = summary
        self.prompts: list[str] = []

    async def summarize(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.summary


# ── estimate_tokens ─────────────────────────────────────────────────


def test_estimate_tokens_close_to_tiktoken_for_english():
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    text = "Hello world. " * 100
    messages = [_msg("user", text)]
    truth = len(enc.encode(text))
    est = estimate_tokens(messages)
    assert abs(est - truth) / truth < 0.10


def test_estimate_tokens_sums_across_messages():
    a = _msg("user", "hello world")
    b = _msg("ai", "你好，世界")
    only_a = estimate_tokens([a])
    only_b = estimate_tokens([b])
    both = estimate_tokens([a, b])
    assert abs(both - (only_a + only_b)) <= 2


def test_estimate_tokens_empty_returns_zero():
    assert estimate_tokens([]) == 0


# ── compact: 阈值未到 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compact_idempotent_when_below_threshold():
    msgs = [
        _msg("user", "改一下颜色"),
        _msg("ai", "好的，改完了"),
    ]
    llm = _FakeLLM()
    out = await compact(msgs, llm=llm)
    assert out == msgs
    assert llm.prompts == []


# ── compact: 触发压缩 ────────────────────────────────────────────────


def _long_text(prefix: str, n: int) -> str:
    return prefix + ("A" * n)


@pytest.mark.asyncio
async def test_compact_keeps_all_user_messages_full():
    msgs: list[dict] = []
    for i in range(8):
        msgs.append(_msg("user", f"需求{i}"))
        msgs.append(_msg("ai", _long_text(f"ai-resp-{i}-", 6000)))

    llm = _FakeLLM(summary="老 AI 回复摘要")
    out = await compact(msgs, llm=llm, threshold_soft=5_000)

    user_in = [m["content"] for m in msgs if m["type"] == "user"]
    user_out = [m["content"] for m in out if m["type"] == "user"]
    for t in user_in:
        assert t in user_out


@pytest.mark.asyncio
async def test_compact_keeps_recent_6_user_ai_pairs():
    msgs: list[dict] = []
    for i in range(10):
        msgs.append(_msg("user", f"需求{i}"))
        msgs.append(_msg("ai", _long_text(f"ai-resp-{i}-", 6000)))

    llm = _FakeLLM(summary="摘要")
    out = await compact(msgs, llm=llm, threshold_soft=5_000)

    recent_ai = [m["content"] for m in msgs[-12:] if m["type"] == "ai"]
    out_ai = [m["content"] for m in out if m["type"] == "ai"]
    for t in recent_ai:
        assert t in out_ai


@pytest.mark.asyncio
async def test_compact_summary_inserted_before_kept_window():
    msgs: list[dict] = []
    for i in range(10):
        msgs.append(_msg("user", f"需求{i}"))
        msgs.append(_msg("ai", _long_text(f"ai-resp-{i}-", 6000)))

    llm = _FakeLLM(summary="历史摘要内容")
    out = await compact(msgs, llm=llm, threshold_soft=5_000)

    summary_idx = next(
        (i for i, m in enumerate(out) if m["type"] == "summary"),
        -1,
    )
    assert summary_idx >= 0
    assert out[summary_idx]["content"] == "历史摘要内容"
    after = out[summary_idx + 1 :]
    assert any(m["type"] == "user" for m in after)
    assert any(m["type"] == "ai" for m in after)


@pytest.mark.asyncio
async def test_compact_calls_llm_with_chinese_summary_prompt():
    msgs: list[dict] = []
    for i in range(10):
        msgs.append(_msg("user", f"需求{i}"))
        msgs.append(_msg("ai", _long_text(f"ai-resp-{i}-", 6000)))

    llm = _FakeLLM(summary="ok")
    await compact(msgs, llm=llm, threshold_soft=5_000)

    assert len(llm.prompts) == 1
    p = llm.prompts[0]
    assert "doskill" in p.lower()
    assert "对话压缩" in p or "压成" in p
    assert "ai-resp-0-" in p


@pytest.mark.asyncio
async def test_compact_preserves_marked_ai_messages():
    msgs: list[dict] = []
    for i in range(8):
        msgs.append(_msg("user", f"需求{i}"))
        msgs.append(_msg("ai", _long_text(f"ai-resp-{i}-", 6000)))
    msgs.insert(4, _msg("ai", "这是关键决策，必须保留", preserve=True))

    llm = _FakeLLM(summary="ok")
    out = await compact(msgs, llm=llm, threshold_soft=5_000)
    out_ai = [m["content"] for m in out if m["type"] == "ai"]
    assert "这是关键决策，必须保留" in out_ai


def test_compaction_prompt_template_constant_exposed():
    assert isinstance(COMPACTION_PROMPT, str)
    assert "doskill" in COMPACTION_PROMPT.lower()
    assert "{messages}" in COMPACTION_PROMPT
