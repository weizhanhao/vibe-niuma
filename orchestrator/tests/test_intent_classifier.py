"""Plan 10 Task 3: LLM-based intent_classifier 测试。

业务员视角：
  - 业务员说「字号大一点」→ LLM 看上下文判 refine_cr（续改上一版）
  - 业务员说「加个搜索」→ LLM 判 new_cr（新需求）
  - 业务员说「这个改得怎么样」→ LLM 判 chat_only（不进 pipeline）
  - 业务员 ⇧⌘↵ 强制 new_cr → override 直接返不调 LLM
  - LLM 出错时兜底走 new_cr（保守 = 别误吃业务员的 refine 意图）

附件**不参与决策**（业务员明示：截图是补充，AI 应靠项目上下文判）
"""
from __future__ import annotations

import pytest

from orchestrator.intent_classifier import (
    IntentClassifier,
    IntentDecision,
)


class _FakeLLM:
    """记录 prompt 的伪 LLM；返回固定 JSON 文本。"""

    def __init__(self, response: str | None = None, raises: bool = False):
        self.response = response or '{"mode":"new_cr","confidence":0.9,"reason":"默认"}'
        self.raises = raises
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, model: str | None = None) -> str:
        self.prompts.append(prompt)
        if self.raises:
            raise RuntimeError("LLM 网络挂了")
        return self.response


def _msgs(*pairs: tuple[str, str]) -> list[dict]:
    """造 chat history：[('user','...'),('ai','...')...]"""
    return [{"type": t, "ts": "t", "content": c} for t, c in pairs]


# ── LLM call + prompt 内容 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_mode_confidence_reason():
    llm = _FakeLLM('{"mode":"refine_cr","confidence":0.85,"reason":"续改上一版"}')
    clf = IntentClassifier(llm=llm)
    decision = await clf.classify(
        message_text="字号大一点",
        conversation_messages=_msgs(("user", "改红"), ("ai", "改完了")),
        last_cr_state="preview-ready",
        repo_doc="",
    )
    assert isinstance(decision, IntentDecision)
    assert decision.mode == "refine_cr"
    assert decision.confidence == 0.85
    assert "续改" in decision.reason


@pytest.mark.asyncio
async def test_classify_prompt_includes_conversation_history():
    llm = _FakeLLM()
    clf = IntentClassifier(llm=llm)
    await clf.classify(
        message_text="字号大一点",
        conversation_messages=_msgs(("user", "改红"), ("ai", "改完了")),
        last_cr_state="preview-ready",
        repo_doc="",
    )
    p = llm.prompts[0]
    assert "改红" in p
    assert "改完了" in p
    assert "字号大一点" in p


@pytest.mark.asyncio
async def test_classify_prompt_includes_repo_doc_summary():
    llm = _FakeLLM()
    clf = IntentClassifier(llm=llm)
    await clf.classify(
        message_text="加个搜索",
        conversation_messages=[],
        last_cr_state=None,
        repo_doc="React 项目，订单管理。路由：/orders, /orders/:id",
    )
    p = llm.prompts[0]
    assert "订单管理" in p or "路由" in p


@pytest.mark.asyncio
async def test_classify_prompt_includes_last_cr_state():
    llm = _FakeLLM()
    clf = IntentClassifier(llm=llm)
    await clf.classify(
        message_text="嗯，再调一下",
        conversation_messages=_msgs(("user", "改红")),
        last_cr_state="preview-ready",
        repo_doc="",
    )
    p = llm.prompts[0]
    assert "preview-ready" in p or "上一个 CR" in p or "上一条 CR" in p


# ── 三种 mode 路由 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_routes_continuation_phrase_to_refine():
    llm = _FakeLLM('{"mode":"refine_cr","confidence":0.9,"reason":"续改"}')
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="字号大一点",
        conversation_messages=_msgs(("user", "改红")),
        last_cr_state="preview-ready",
        repo_doc="",
    )
    assert d.mode == "refine_cr"


@pytest.mark.asyncio
async def test_classify_routes_question_phrase_to_chat_only():
    llm = _FakeLLM('{"mode":"chat_only","confidence":0.8,"reason":"讨论"}')
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="你觉得这次改得怎么样？",
        conversation_messages=_msgs(("user", "改红")),
        last_cr_state="merged",
        repo_doc="",
    )
    assert d.mode == "chat_only"


@pytest.mark.asyncio
async def test_classify_routes_new_intent_to_new_cr_regardless_of_history():
    llm = _FakeLLM('{"mode":"new_cr","confidence":0.95,"reason":"新需求"}')
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="加个搜索",
        conversation_messages=_msgs(("user", "改红"), ("ai", "改完")),
        last_cr_state="preview-ready",
        repo_doc="",
    )
    assert d.mode == "new_cr"


# ── confidence 低 + override + 兜底 ────────────────────────────────


@pytest.mark.asyncio
async def test_classify_low_confidence_returns_unsure_flag():
    llm = _FakeLLM('{"mode":"refine_cr","confidence":0.45,"reason":"不太确定"}')
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="再来一下",
        conversation_messages=_msgs(("user", "改红")),
        last_cr_state="preview-ready",
        repo_doc="",
    )
    assert d.is_unsure
    assert d.mode == "refine_cr"


@pytest.mark.asyncio
async def test_classify_high_confidence_not_unsure():
    llm = _FakeLLM('{"mode":"new_cr","confidence":0.95,"reason":"清晰"}')
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="加搜索",
        conversation_messages=[],
        last_cr_state=None,
        repo_doc="",
    )
    assert not d.is_unsure


@pytest.mark.asyncio
async def test_classify_explicit_override_force_new_cr_skips_llm():
    """业务员 ⇧⌘↵：强制 new_cr，LLM 不调（省 cost）。"""
    llm = _FakeLLM()
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="字号大一点",
        conversation_messages=_msgs(("user", "改红")),
        last_cr_state="preview-ready",
        repo_doc="",
        override="new_cr",
    )
    assert d.mode == "new_cr"
    assert d.confidence == 1.0
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_classify_explicit_override_force_refine_skips_llm():
    llm = _FakeLLM()
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="加搜索",
        conversation_messages=[],
        last_cr_state=None,
        repo_doc="",
        override="refine_cr",
    )
    assert d.mode == "refine_cr"
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_classify_falls_back_to_new_cr_on_llm_error():
    """LLM 网络挂了：默认 new_cr + confidence=0.5 + is_unsure=True。"""
    llm = _FakeLLM(raises=True)
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="改一下",
        conversation_messages=[],
        last_cr_state=None,
        repo_doc="",
    )
    assert d.mode == "new_cr"
    assert d.confidence == 0.5
    assert d.is_unsure


@pytest.mark.asyncio
async def test_classify_falls_back_on_unparseable_llm_response():
    """LLM 返非 JSON 文本：兜底 new_cr。"""
    llm = _FakeLLM("我也不知道该走哪个 mode 哈哈")
    clf = IntentClassifier(llm=llm)
    d = await clf.classify(
        message_text="改一下",
        conversation_messages=[],
        last_cr_state=None,
        repo_doc="",
    )
    assert d.mode == "new_cr"
    assert d.is_unsure
