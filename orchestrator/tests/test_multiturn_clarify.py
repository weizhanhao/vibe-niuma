"""Plan 10 Task 8: BrainstormingSkill 真多轮 + 强制 options 测试。

业务员视角（用户原话）：「不要固定问最多三个问题，要到 AI 完全理解；
让用户去选择，AI 根据代码和页面的理解给业务员选项，没合适的可以自定义。」
"""
from __future__ import annotations

import json

import pytest

from orchestrator.adapters.impl.brainstorming_skill import (
    BrainstormingSkill,
    MAX_SOFT_ROUNDS,
    STOP_CLARIFY_SENTINEL,
)
from orchestrator.adapters.types import RawRequest


class _ScriptedLLM:
    """按 plan_responses 顺序返；每次复用最后一个（防 IndexError）。"""

    def __init__(self, plan_responses: list[str]):
        self._responses = plan_responses
        self._calls = 0
        self.prompts: list[str] = []

    async def complete_vision(self, prompt: str, image_b64: str, **kw) -> str:
        self._calls += 1
        self.prompts.append(prompt)
        idx = min(self._calls - 1, len(self._responses) - 1)
        return self._responses[idx]

    async def complete_vision_stream(self, prompt: str, image_b64: str,
                                     on_token, **kw) -> str:
        return await self.complete_vision(prompt, image_b64, **kw)

    @property
    def call_count(self) -> int:
        return self._calls


class _ScriptedChannel:
    """按 answers 顺序回；记录 ask / present_variants 的调用。"""

    def __init__(self, answers: list[str] | None = None,
                 variant_selection: str | None = None):
        self._answers = answers or []
        self._variant_selection = variant_selection
        self.ask_calls: list[tuple[str, list[str] | None]] = []
        self.variants_calls: list = []

    async def ask(self, question: str, options=None) -> str:
        self.ask_calls.append((question, options))
        if not self._answers:
            return ""
        return self._answers.pop(0)

    async def present_variants(self, variants):
        from orchestrator.adapters.types import VariantSelection
        self.variants_calls.append(variants)
        return VariantSelection(selected_id=self._variant_selection)


def _raw(text: str = "改") -> RawRequest:
    return RawRequest(
        url="http://x/orders", screenshot_b64="img",
        box_coords={}, viewport={}, request_text=text,
    )


def _plan_round(done: bool, *, question: str = "", options: list[str] | None = None,
                weight: str = "light") -> str:
    return json.dumps({
        "weight": weight,
        "done": done,
        "question": question,
        "options": options or [],
    })


def _plan_heavy_variants(variants: list[dict]) -> str:
    return json.dumps({
        "weight": "heavy",
        "variants": variants,
    })


# ── 真多轮 loop ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clarify_loops_until_llm_returns_done_true():
    """LLM 连续返 done=false 三轮，第 4 轮 done=true → channel.ask 被调 3 次。"""
    llm = _ScriptedLLM([
        _plan_round(False, question="按啥字段搜？", options=["订单号", "客户名", "全文搜", "自己描述"]),
        _plan_round(False, question="搜出来要排序吗？", options=["按时间", "按金额", "不排序", "自己描述"]),
        _plan_round(False, question="搜结果默认显示几条？", options=["10", "20", "50", "自己描述"]),
        _plan_round(True),
    ])
    channel = _ScriptedChannel(answers=["订单号", "按时间", "20"])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)

    brief = await skill.clarify(_raw("加搜索"), channel)

    assert len(channel.ask_calls) == 3
    assert len(brief.clarifications) == 3
    assert brief.clarifications[0]["answer"] == "订单号"


@pytest.mark.asyncio
async def test_clarify_each_round_passes_previous_answers_in_prompt():
    """第二轮 LLM 调用的 prompt 应包含第一轮的 Q&A，让它知道已答什么。"""
    llm = _ScriptedLLM([
        _plan_round(False, question="按啥字段搜？", options=["订单号", "客户名", "自己描述"]),
        _plan_round(False, question="UNIQUE_Q_2", options=["a", "b"]),
        _plan_round(True),
    ])
    channel = _ScriptedChannel(answers=["订单号", "a"])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    await skill.clarify(_raw("加搜索"), channel)

    assert llm.call_count >= 2
    second_prompt = llm.prompts[1]
    assert "按啥字段搜" in second_prompt
    assert "订单号" in second_prompt


@pytest.mark.asyncio
async def test_clarify_soft_cap_8_rounds_force_break():
    """LLM 永远 done=false → 第 8 轮强制 break。"""
    llm = _ScriptedLLM([
        _plan_round(False, question=f"Q{i}", options=["a", "b"])
        for i in range(20)
    ])
    channel = _ScriptedChannel(answers=["a"] * 20)
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)

    brief = await skill.clarify(_raw("?"), channel)

    assert MAX_SOFT_ROUNDS == 8
    assert len(channel.ask_calls) <= MAX_SOFT_ROUNDS
    assert len(brief.clarifications) <= MAX_SOFT_ROUNDS


@pytest.mark.asyncio
async def test_clarify_user_stop_sentinel_breaks_loop():
    """业务员主动按「✓ 够了直接干」→ 发 __STOP_CLARIFY__ → loop 立刻 break。"""
    llm = _ScriptedLLM([
        _plan_round(False, question="Q1", options=["a", "b"]),
        _plan_round(False, question="Q2", options=["a", "b"]),
        _plan_round(False, question="Q3", options=["a", "b"]),
    ])
    channel = _ScriptedChannel(answers=["第一答", STOP_CLARIFY_SENTINEL])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)

    brief = await skill.clarify(_raw("?"), channel)

    assert len(channel.ask_calls) == 2
    assert len(brief.clarifications) == 1
    assert brief.clarifications[0]["answer"] == "第一答"


@pytest.mark.asyncio
async def test_clarify_options_passed_to_channel_ask():
    """LLM 给的 options 透传到 channel.ask。"""
    llm = _ScriptedLLM([
        _plan_round(False, question="Q1", options=["选项A", "选项B", "自己描述"]),
        _plan_round(True),
    ])
    channel = _ScriptedChannel(answers=["选项A"])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    await skill.clarify(_raw("?"), channel)

    assert len(channel.ask_calls) == 1
    q, options = channel.ask_calls[0]
    assert q == "Q1"
    assert options == ["选项A", "选项B", "自己描述"]


@pytest.mark.asyncio
async def test_clarify_first_round_done_true_no_ask():
    """LLM 第一轮就说 done=true → 完全不问业务员（简单微调不打扰）。"""
    llm = _ScriptedLLM([_plan_round(True)])
    channel = _ScriptedChannel()
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)

    brief = await skill.clarify(_raw("把按钮改蓝"), channel)
    assert len(channel.ask_calls) == 0
    assert brief.clarifications == []


# ── heavy 路径仍走 variants 不进 loop ─────────────────────────────


@pytest.mark.asyncio
async def test_clarify_heavy_path_presents_variants_no_loop():
    llm = _ScriptedLLM([
        _plan_heavy_variants([
            {"id": "v1", "title": "方案 1", "html": "<div>1</div>"},
            {"id": "v2", "title": "方案 2", "html": "<div>2</div>"},
        ]),
    ])
    channel = _ScriptedChannel(variant_selection="v2")
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    brief = await skill.clarify(_raw("重新设计页面"), channel)

    assert len(channel.variants_calls) == 1
    assert len(channel.ask_calls) == 0
    assert brief.selected_mockup is not None
    assert brief.selected_mockup.id == "v2"
