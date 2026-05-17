"""BrainstormingSkill 多轮澄清测试（unknowns 驱动版本）。

业务员原话（多次反馈整合）：
- 「不要固定问最多三个问题，要到 AI 完全理解」
- 「让用户去选择，AI 根据代码和页面的理解给业务员选项，没合适的可以自定义」
- 「不是换关键词，而是 ai 自己判断是不是理解了；这和几轮没关系，是搞懂需求」

设计：LLM 每轮列出 `unknowns: [...]`。空数组 ⇒ done；非空 ⇒ 必须再问。
没有关键词 precheck，没有软上限；只有 _HARD_ROUND_CAP=12 做死循环保险。
"""
from __future__ import annotations

import json

import pytest

from orchestrator.adapters.impl.brainstorming_skill import (
    BrainstormingSkill,
    STOP_CLARIFY_SENTINEL,
    _HARD_ROUND_CAP,
)
from orchestrator.adapters.types import RawRequest


class _ScriptedLLM:
    """Mock 两个接口：
    - `complete()`：text-only，按 plan_responses 顺序返（每轮 _plan 用这个）
    - `complete_vision*()`：第一轮 _describe_screen 用，返固定 vision_response
    """

    def __init__(self, plan_responses: list[str],
                 vision_response: str = "订单列表页面，含状态下拉框"):
        self._plan_responses = plan_responses
        self._vision_response = vision_response
        self.text_calls = 0
        self.vision_calls = 0
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, model: str | None = None) -> str:
        self.text_calls += 1
        self.prompts.append(prompt)
        idx = min(self.text_calls - 1, len(self._plan_responses) - 1)
        return self._plan_responses[idx]

    async def complete_vision(self, prompt: str, image_b64: str, **kw) -> str:
        self.vision_calls += 1
        return self._vision_response

    async def complete_vision_stream(self, prompt: str, image_b64: str,
                                     on_token, **kw) -> str:
        return await self.complete_vision(prompt, image_b64, **kw)

    async def complete_vision_multi(self, prompt: str, images, **kw) -> str:
        return await self.complete_vision(prompt, "", **kw)

    @property
    def call_count(self) -> int:
        """历史 API：原指 LLM 总调次。新设计区分 vision/text，这里返 text 数。"""
        return self.text_calls


class _ScriptedChannel:
    """支持 ask / ask_rich / present_form / present_variants 全套。

    answers: 单题（ask/ask_rich）按顺序消耗
    form_answers: 多题表单按顺序消耗（每项是 {question: answer} dict）
    """
    def __init__(self, answers: list[str] | None = None,
                 variant_selection: str | None = None,
                 form_answers: list[dict] | None = None):
        self._answers = answers or []
        self._variant_selection = variant_selection
        self._form_answers = form_answers or []
        self.ask_calls: list[tuple[str, list[str] | None]] = []
        self.ask_rich_calls: list[tuple[str, list[str] | None, str | None]] = []
        self.variants_calls: list = []
        self.form_calls: list[list[dict]] = []

    async def ask(self, question: str, options=None) -> str:
        self.ask_calls.append((question, options))
        if not self._answers:
            return ""
        return self._answers.pop(0)

    async def ask_rich(self, question: str, options=None, *, recommended=None) -> str:
        # 既记 rich 调用（带 recommended），也 mirror 进 ask_calls 让老断言
        # 不区分单题路径继续工作。
        self.ask_rich_calls.append((question, options, recommended))
        self.ask_calls.append((question, options))
        if not self._answers:
            return ""
        return self._answers.pop(0)

    async def present_form(self, questions: list[dict]) -> dict:
        self.form_calls.append(questions)
        if not self._form_answers:
            return {}
        return self._form_answers.pop(0)

    async def present_variants(self, variants):
        from orchestrator.adapters.types import VariantSelection
        self.variants_calls.append(variants)
        return VariantSelection(selected_id=self._variant_selection)


def _raw(text: str = "改") -> RawRequest:
    return RawRequest(
        url="http://x/orders", screenshot_b64="img",
        box_coords={}, viewport={}, request_text=text,
    )


def _plan_round(
    unknowns: list[str] | bool,
    *, question: str = "", options: list[str] | None = None,
    recommended: str | None = None,
    weight: str = "light",
    questions: list[dict] | None = None,
) -> str:
    """Plan 工厂。

    传 bool：True ⇒ unknowns=[]（done），False ⇒ unknowns=['(需要再问)']
    传 list：直接用作 unknowns
    questions 优先（多题表单）；否则 question + options 平铺（单题）
    """
    if isinstance(unknowns, bool):
        u = [] if unknowns else ["（需要再问）"]
    else:
        u = unknowns
    body: dict = {"weight": weight, "unknowns": u}
    if questions is not None:
        body["questions"] = questions
    else:
        body["question"] = question
        body["options"] = options or []
        if recommended:
            body["recommended"] = recommended
    return json.dumps(body)


def _q(question: str, options: list[str], *,
       multi: bool = False, recommended: str | None = None) -> dict:
    """One question entry for `questions` array."""
    q: dict = {"question": question, "options": options, "multi": multi}
    if recommended:
        q["recommended"] = recommended
    return q


def _plan_heavy_variants(variants: list[dict]) -> str:
    return json.dumps({
        "weight": "heavy",
        "variants": variants,
    })


# ── unknowns 驱动 loop ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clarify_loops_until_unknowns_empty():
    """LLM 连返 unknowns 非空三轮，第 4 轮 unknowns=[] → channel.ask 调 3 次。"""
    llm = _ScriptedLLM([
        _plan_round(False, question="按啥字段搜？", options=["订单号", "客户名"]),
        _plan_round(False, question="搜出来要排序吗？", options=["按时间", "按金额"]),
        _plan_round(False, question="结果显示几条？", options=["10", "20"]),
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
    """每轮 prompt 必须包含之前所有 Q&A，LLM 才能基于历史判断 unknowns。"""
    llm = _ScriptedLLM([
        _plan_round(False, question="按啥字段搜？", options=["订单号"]),
        _plan_round(False, question="UNIQUE_Q_2", options=["a", "b"]),
        _plan_round(True),
    ])
    channel = _ScriptedChannel(answers=["订单号", "a"])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    await skill.clarify(_raw("加搜索"), channel)

    assert llm.text_calls >= 2
    second_prompt = llm.prompts[1]
    assert "按啥字段搜" in second_prompt
    assert "订单号" in second_prompt


@pytest.mark.asyncio
async def test_clarify_hard_cap_protects_against_llm_infinite_loop():
    """LLM 永远 unknowns 非空 → 到硬上限 _HARD_ROUND_CAP=12 强制 break。"""
    llm = _ScriptedLLM([
        _plan_round(False, question=f"Q{i}", options=["a", "b"])
        for i in range(50)
    ])
    channel = _ScriptedChannel(answers=["a"] * 50)
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)

    brief = await skill.clarify(_raw("?"), channel)

    assert _HARD_ROUND_CAP == 12
    assert len(channel.ask_calls) <= _HARD_ROUND_CAP
    assert len(brief.clarifications) <= _HARD_ROUND_CAP


@pytest.mark.asyncio
async def test_clarify_user_stop_sentinel_breaks_loop():
    """业务员按「✓ 够了直接干」→ 发 __STOP_CLARIFY__ → loop 立刻 break。"""
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
        _plan_round(False, question="Q1", options=["选项A", "选项B", "我自己描述"]),
        _plan_round(True),
    ])
    channel = _ScriptedChannel(answers=["选项A"])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    await skill.clarify(_raw("?"), channel)

    assert len(channel.ask_calls) == 1
    q, options = channel.ask_calls[0]
    assert q == "Q1"
    assert options == ["选项A", "选项B", "我自己描述"]


@pytest.mark.asyncio
async def test_clarify_unknowns_empty_first_round_no_ask():
    """LLM 第一轮就返 unknowns=[]（需求已清晰）→ 不问业务员。"""
    llm = _ScriptedLLM([_plan_round(True)])
    channel = _ScriptedChannel()
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)

    brief = await skill.clarify(_raw("把按钮背景改成 #1890ff"), channel)
    assert len(channel.ask_calls) == 0
    assert brief.clarifications == []


# ── unknowns 内容驱动行为 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_clarify_first_round_calls_vision_once_for_screen_describe():
    """第一轮先调 complete_vision 描述截图 → 后续 text 调用拿到 screen_context。"""
    llm = _ScriptedLLM([_plan_round(True)], vision_response="订单列表页面")
    channel = _ScriptedChannel()
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    await skill.clarify(_raw("加搜索"), channel)

    # vision 1 次（describe_screen），text 1 次（_plan）
    assert llm.vision_calls == 1
    assert llm.text_calls == 1
    # text prompt 里要带 vision 输出
    assert "订单列表页面" in llm.prompts[0]


@pytest.mark.asyncio
async def test_clarify_vision_cached_across_rounds():
    """多轮内 vision 只调一次，后续轮复用缓存。"""
    llm = _ScriptedLLM([
        _plan_round(False, question="Q1", options=["a", "b"]),
        _plan_round(False, question="Q2", options=["a", "b"]),
        _plan_round(True),
    ])
    channel = _ScriptedChannel(answers=["a", "a"])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    await skill.clarify(_raw("?"), channel)

    # 三轮 _plan 调用，vision 仍只 1 次
    assert llm.text_calls == 3
    assert llm.vision_calls == 1


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_open_question_not_done():
    """LLM 报错时绝不默认 done，给业务员一个开放兜底问题。"""
    class _ExplodingLLM(_ScriptedLLM):
        async def complete(self, prompt, *, model=None):
            raise RuntimeError("api down")

        async def complete_vision(self, prompt, image_b64, **kw):
            raise RuntimeError("vision also down")

    llm = _ExplodingLLM([_plan_round(True)])
    channel = _ScriptedChannel(answers=[STOP_CLARIFY_SENTINEL])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)

    brief = await skill.clarify(_raw("?"), channel)

    # LLM 全挂 → 兜底问题，业务员被问了一次
    assert len(channel.ask_calls) == 1
    q, options = channel.ask_calls[0]
    # 兜底问题必须明示 AI 出错 + 提供「直接描述」逃生口
    assert "AI" in q and ("出错" in q or "不可用" in q or "没能" in q)
    assert options is not None and "我自己描述" in options
    # STOP → 没产生 clarifications
    assert brief.clarifications == []


# ── 多题表单 + 推荐项 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_question_with_recommended_passes_to_ask_rich():
    """单题带 recommended → 走 ask_rich 路径，channel 拿到推荐项标记。"""
    llm = _ScriptedLLM([
        _plan_round(
            False, question="想换成什么 UI？",
            options=["按钮组", "tab", "segmented", "我自己描述"],
            recommended="segmented",
        ),
        _plan_round(True),
    ])
    channel = _ScriptedChannel(answers=["segmented"])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    await skill.clarify(_raw("把下拉框换掉"), channel)

    assert len(channel.ask_rich_calls) == 1
    q, options, recommended = channel.ask_rich_calls[0]
    assert recommended == "segmented"
    assert "segmented" in options


@pytest.mark.asyncio
async def test_multi_question_form_path():
    """LLM 返 questions=[Q1, Q2] → present_form 一次问完两题。"""
    llm = _ScriptedLLM([
        _plan_round(False, questions=[
            _q("换成什么 UI？", ["按钮组", "tab", "我自己描述"], recommended="按钮组"),
            _q("保留哪些状态？", ["全部", "已支付", "待支付"], multi=True),
        ]),
        _plan_round(True),
    ])
    channel = _ScriptedChannel(form_answers=[{
        "换成什么 UI？": "按钮组",
        "保留哪些状态？": "全部 / 已支付",
    }])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    brief = await skill.clarify(_raw("改下拉框"), channel)

    # 一次 form 调用，不走单题 ask
    assert len(channel.form_calls) == 1
    assert len(channel.ask_calls) == 0
    assert len(channel.ask_rich_calls) == 0
    # 两题都被序列化进 clarifications
    assert len(brief.clarifications) == 2
    qs = {c["question"] for c in brief.clarifications}
    assert qs == {"换成什么 UI？", "保留哪些状态？"}
    # multi-select 用 / 分隔
    multi_answer = next(c["answer"] for c in brief.clarifications if c["question"] == "保留哪些状态？")
    assert "全部" in multi_answer and "已支付" in multi_answer


@pytest.mark.asyncio
async def test_multi_question_form_with_stop_breaks_loop():
    """业务员在表单里按 STOP → form 返回 {__stop__: True} → loop 立刻 break。"""
    llm = _ScriptedLLM([
        _plan_round(False, questions=[
            _q("Q1", ["a", "b"]),
            _q("Q2", ["x", "y"]),
        ]),
    ])
    channel = _ScriptedChannel(form_answers=[{"__stop__": True}])
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    brief = await skill.clarify(_raw("?"), channel)

    assert len(channel.form_calls) == 1
    # STOP → 没产生 clarifications
    assert brief.clarifications == []


@pytest.mark.asyncio
async def test_recommended_only_valid_if_matches_options():
    """LLM 写了一个 options 里没有的 recommended → 解析端清成 None，UI 不会乱高亮。"""
    from orchestrator.adapters.impl.brainstorming_skill import _parse_unknowns
    text = json.dumps({
        "weight": "light",
        "unknowns": ["x"],
        "question": "?",
        "options": ["A", "B"],
        "recommended": "C",  # 不在 options 里
    })
    parsed = _parse_unknowns(text)
    assert len(parsed["questions"]) == 1
    assert parsed["questions"][0]["recommended"] is None


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
