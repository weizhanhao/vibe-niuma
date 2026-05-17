"""BrainstormingSkill 契约测试 —— mock LLM，验证轻/重路径分流 + 约束。"""
from __future__ import annotations

import json

import pytest

from orchestrator.adapters.impl._llm import LLMClient
from orchestrator.adapters.impl.brainstorming_skill import (
    TECH_CONSTRAINT,
    BrainstormingSkill,
)
from orchestrator.adapters.types import HtmlMockup, RawRequest, VariantSelection


def _raw(text: str = "把保存按钮改蓝") -> RawRequest:
    return RawRequest(
        url="http://demo/settings",
        screenshot_b64="img-b64",
        box_coords={"x": 1},
        viewport={"width": 1280},
        request_text=text,
    )


class FakeChannel:
    """脚本化的 channel：提前塞回答；按调用顺序消耗。"""

    def __init__(self, answers: list[str] | None = None, variant_pick: str | None = None):
        self._answers = list(answers or [])
        self._variant_pick = variant_pick
        self.asked: list[str] = []
        self.variants_shown: list[list[HtmlMockup]] = []

    async def ask(self, question: str, options) -> str:
        self.asked.append(question)
        return self._answers.pop(0) if self._answers else ""

    async def present_variants(self, variants):
        self.variants_shown.append(list(variants))
        return VariantSelection(selected_id=self._variant_pick)


def _stub_llm(monkeypatch, response: str) -> LLMClient:
    """造一个 LLMClient 实例，并把它的 complete_vision 替换成返回固定字符串的 stub。"""
    client = LLMClient(base_url="http://x", api_key="k", default_model="m")

    async def _stub(*a, **kw):
        return response

    monkeypatch.setattr(client, "complete_vision", _stub)
    return client


# ── 轻路径 ──────────────────────────────────────────────────────────

@pytest.mark.skip(reason="[Plan 10 obsolete] uses legacy questions:[] JSON shape; multi-turn loop tested in test_multiturn_clarify.py")
async def test_clarify_light_path_asks_text_questions(monkeypatch):
    plan = json.dumps({
        "weight": "light",
        "questions": ["想让谁更显眼？", "需要保留原配色吗？"],
    })
    skill = BrainstormingSkill(_stub_llm(monkeypatch, plan))
    chan = FakeChannel(answers=["待付款", "保留"])
    brief = await skill.clarify(_raw(), chan)
    assert chan.asked == ["想让谁更显眼？", "需要保留原配色吗？"]
    assert len(brief.clarifications) == 2
    assert brief.clarifications[0]["answer"] == "待付款"
    assert brief.selected_mockup is None


@pytest.mark.skip(reason="[Plan 10 obsolete] max_questions cap replaced by MAX_SOFT_ROUNDS + LLM done=true; see test_multiturn_clarify.py::test_clarify_soft_cap_8_rounds_force_break")
async def test_clarify_respects_max_three_questions(monkeypatch):
    plan = json.dumps({
        "weight": "light",
        "questions": [f"问题 {i}" for i in range(8)],
    })
    skill = BrainstormingSkill(_stub_llm(monkeypatch, plan), max_questions=3)
    chan = FakeChannel(answers=["a", "b", "c", "d", "e"])
    await skill.clarify(_raw(), chan)
    assert len(chan.asked) == 3


@pytest.mark.skip(reason="[Plan 10 obsolete] uses legacy questions:[] JSON shape; skip-answer behavior tested in test_multiturn_clarify.py")
async def test_clarify_skipped_answers_do_not_pollute_brief(monkeypatch):
    plan = json.dumps({"weight": "light", "questions": ["q1", "q2"]})
    skill = BrainstormingSkill(_stub_llm(monkeypatch, plan))
    chan = FakeChannel(answers=["", "跳过"])
    brief = await skill.clarify(_raw(), chan)
    assert brief.clarifications == []  # 两个都被跳过


# ── 重路径 ──────────────────────────────────────────────────────────

async def test_clarify_heavy_path_presents_variants_and_records_pick(monkeypatch):
    plan = json.dumps({
        "weight": "heavy",
        "variants": [
            {"id": "a", "title": "A 方向", "html": "<div>A</div>"},
            {"id": "b", "title": "B 方向", "html": "<div>B</div>"},
            {"id": "c", "title": "C 方向", "html": "<div>C</div>"},
        ],
    })
    skill = BrainstormingSkill(_stub_llm(monkeypatch, plan))
    chan = FakeChannel(variant_pick="b")
    brief = await skill.clarify(_raw("重排首页卡片"), chan)
    assert len(chan.variants_shown) == 1
    assert len(chan.variants_shown[0]) == 3
    assert brief.selected_mockup is not None
    assert brief.selected_mockup.id == "b"
    assert brief.clarifications == []  # 重路径不问文字问题


async def test_clarify_heavy_path_reject_all_returns_no_mockup(monkeypatch):
    plan = json.dumps({
        "weight": "heavy",
        "variants": [{"id": "a", "title": "A", "html": "<a/>"}],
    })
    skill = BrainstormingSkill(_stub_llm(monkeypatch, plan))
    chan = FakeChannel(variant_pick=None)  # 全否
    brief = await skill.clarify(_raw(), chan)
    assert brief.selected_mockup is None


# ── 约束 / 降级 ──────────────────────────────────────────────────────

def test_plan_prompt_contains_tech_constraint():
    skill = BrainstormingSkill(LLMClient(base_url="x", api_key="k", default_model="m"))
    text = skill._build_plan_prompt(_raw(), repo_doc="")
    assert TECH_CONSTRAINT in text
    assert "只能问业务" in text
    assert "禁止涉及任何技术" in text


async def test_clarify_falls_back_to_skip_when_llm_returns_garbage(monkeypatch):
    skill = BrainstormingSkill(_stub_llm(monkeypatch, "this is not json at all"))
    chan = FakeChannel()
    brief = await skill.clarify(_raw(), chan)
    assert brief.clarifications == []
    assert brief.selected_mockup is None


async def test_clarify_falls_back_when_llm_raises(monkeypatch):
    client = LLMClient(base_url="http://x", api_key="k", default_model="m")

    async def _bad(*a, **kw):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(client, "complete_vision", _bad)
    skill = BrainstormingSkill(client)
    chan = FakeChannel()
    brief = await skill.clarify(_raw(), chan)
    assert brief.original_text == "把保存按钮改蓝"
    assert brief.clarifications == []
