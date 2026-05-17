"""Plan 10 Task 10: POST /conversations/{id}/messages 端点 + 意图路由。

业务员视角（用户原话）：「不要每次对话都是一个新对话，要像 cursor 一样...
不是每次输入都要截图...用户可以多输入几张图」。

设计要点（spec §Architecture）：
- 同一对话内多轮 message；intent_classifier 决定每条 message 走 new_cr / refine_cr / chat_only
- new_cr / refine_cr → 起 pipeline；chat_only → 调 ChatResponder 直接回
- 端点先 append user message 再 dispatch（即使 LLM 调用失败也不丢历史）
- 返回 `{message_id, mode, cr_id?, ai_message_id?, confidence, is_unsure, reason}`
"""
from __future__ import annotations

from orchestrator.intent_classifier import IntentDecision


# ── 测试辅助 ───────────────────────────────────────────────────────


class _FakeIntentClassifier:
    """脚本化分类器：固定返指定的 decision。"""

    def __init__(self, mode: str = "new_cr", confidence: float = 0.85, reason: str = "ok"):
        self.mode = mode
        self.confidence = confidence
        self.reason = reason
        self.classify_calls: list[dict] = []

    async def classify(self, **kw):
        self.classify_calls.append(kw)
        return IntentDecision(
            mode=kw.get("override") or self.mode,  # type: ignore[arg-type]
            confidence=1.0 if kw.get("override") else self.confidence,
            reason=self.reason,
        )


class _FakeChatResponder:
    """脚本化 chat 回应器：record + append AI message + return reply。"""

    def __init__(self, reply: str = "我帮你想想看 ✓"):
        self.reply = reply
        self.respond_calls: list[dict] = []
        self._conv_repo = None  # 由 factory 注入

    def bind_repo(self, repo):
        self._conv_repo = repo
        return self

    async def respond(self, *, conversation_id, user_message, repo_doc=""):
        self.respond_calls.append({
            "conversation_id": conversation_id,
            "user_message": user_message,
            "repo_doc": repo_doc,
        })
        from datetime import datetime
        self._conv_repo.append_message(conversation_id, {
            "type": "ai", "ts": datetime.utcnow().isoformat(), "content": self.reply,
        })
        return self.reply


def _inject_test_factories(intent_mode: str = "new_cr",
                           chat_reply: str = "我帮你想想看 ✓"):
    """把 fake classifier / chat_responder 灌到 app_state。"""
    from orchestrator.main import app_state
    classifier = _FakeIntentClassifier(mode=intent_mode)
    chat_responder = _FakeChatResponder(reply=chat_reply)

    app_state.intent_classifier = classifier

    def _make_chat_responder(db):
        from orchestrator.conversation import ConversationRepository
        chat_responder.bind_repo(ConversationRepository(db))
        return chat_responder
    app_state.chat_responder_factory = _make_chat_responder
    return classifier, chat_responder


# ── 基础：端点存在 + 返回结构 ────────────────────────────────────


def test_post_messages_returns_message_id_and_mode(client):
    _inject_test_factories(intent_mode="chat_only")

    from orchestrator.main import app_state
    from orchestrator.conversation import ConversationRepository
    conv = ConversationRepository(app_state.session_factory()).create()

    resp = client.post(
        f"/conversations/{conv.id}/messages",
        json={"text": "你觉得改的怎么样？"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "message_id" in body
    assert body["mode"] == "chat_only"
    assert "confidence" in body
    assert "is_unsure" in body


def test_post_messages_404_when_conversation_missing(client):
    _inject_test_factories()
    resp = client.post(
        "/conversations/not-a-real-id/messages",
        json={"text": "hi"},
    )
    assert resp.status_code == 404


# ── 三模式路由 ───────────────────────────────────────────────────


def test_post_messages_chat_only_calls_chat_responder_no_cr(client):
    """chat_only → ChatResponder.respond 被调；不创建 CR。"""
    _, chat = _inject_test_factories(intent_mode="chat_only", chat_reply="嗯改得不错")
    from orchestrator.main import app_state
    from orchestrator.conversation import ConversationRepository
    from orchestrator.models import ChangeRequest

    db = app_state.session_factory()
    conv = ConversationRepository(db).create()
    before_cr_count = db.query(ChangeRequest).count()

    resp = client.post(
        f"/conversations/{conv.id}/messages",
        json={"text": "改得怎么样"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "chat_only"
    assert body.get("cr_id") is None
    assert body.get("ai_message_id") is not None

    assert len(chat.respond_calls) == 1
    assert chat.respond_calls[0]["conversation_id"] == conv.id
    assert chat.respond_calls[0]["user_message"] == "改得怎么样"

    db.expire_all()
    after_cr_count = db.query(ChangeRequest).count()
    assert after_cr_count == before_cr_count


def test_post_messages_new_cr_creates_cr_and_spawns_pipeline(client):
    """new_cr → repo.create(...) + pipeline.run(mode='new_cr') 起来。"""
    _inject_test_factories(intent_mode="new_cr")
    from orchestrator.main import app_state
    from orchestrator.conversation import ConversationRepository
    db = app_state.session_factory()
    conv = ConversationRepository(db).create()

    resp = client.post(
        f"/conversations/{conv.id}/messages",
        json={
            "text": "加个搜索框",
            "attachments": [
                {"kind": "framed_region", "mime": "image/png", "b64": "AAA"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "new_cr"
    assert body["cr_id"]

    assert body["cr_id"] in app_state.pipelines


def test_post_messages_refine_cr_when_classifier_says_so(client):
    """classifier 返 refine_cr → CR.mode=refine_cr + refine_of 指向 base。"""
    _inject_test_factories(intent_mode="refine_cr")
    from orchestrator.main import app_state
    from orchestrator.conversation import ConversationRepository
    from orchestrator.repository import ChangeRequestRepository
    from orchestrator.adapters.types import RawRequest

    db = app_state.session_factory()
    conv = ConversationRepository(db).create()

    repo = ChangeRequestRepository(db)
    raw = RawRequest(url="http://x", screenshot_b64="", box_coords={},
                     viewport={}, request_text="加一个按钮")
    base_cr = repo.create(raw, conversation_id=conv.id)
    from orchestrator.states import State
    base_cr.state = State.PREVIEW_READY.value
    base_cr.branch = "cr/base-branch"
    base_cr.preview_url = "http://localhost:5100"
    base_cr.preview_handle = "fake-handle"
    db.commit()

    resp = client.post(
        f"/conversations/{conv.id}/messages",
        json={"text": "字号再大点"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "refine_cr"
    db.expire_all()
    new_cr = repo.get(body["cr_id"])
    assert new_cr is not None
    assert new_cr.mode == "refine_cr"
    assert new_cr.refine_of == base_cr.id


# ── 持久化：user message 写到 conversation 在 dispatch 之前 ────────


def test_post_messages_persists_user_message_to_conversation(client):
    """user message 应该被 append 到 conversation.messages（含 attachments）。"""
    _inject_test_factories(intent_mode="chat_only")
    from orchestrator.main import app_state
    from orchestrator.conversation import ConversationRepository
    db = app_state.session_factory()
    conv_repo = ConversationRepository(db)
    conv = conv_repo.create()

    resp = client.post(
        f"/conversations/{conv.id}/messages",
        json={
            "text": "这是用户原话",
            "attachments": [
                {"kind": "framed_region", "mime": "image/png", "b64": "AAA"},
            ],
        },
    )
    assert resp.status_code == 200

    db.expire_all()
    fresh = conv_repo.get(conv.id)
    user_msgs = [m for m in (fresh.messages or []) if m.get("type") == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "这是用户原话"
    assert user_msgs[0].get("attachments") is not None
    assert len(user_msgs[0]["attachments"]) == 1


# ── classifier 入参 ───────────────────────────────────────────────


def test_post_messages_passes_last_cr_state_to_classifier(client):
    classifier, _ = _inject_test_factories(intent_mode="new_cr")
    from orchestrator.main import app_state
    from orchestrator.conversation import ConversationRepository
    from orchestrator.repository import ChangeRequestRepository
    from orchestrator.adapters.types import RawRequest

    db = app_state.session_factory()
    conv = ConversationRepository(db).create()
    repo = ChangeRequestRepository(db)
    raw = RawRequest(url="http://x", screenshot_b64="", box_coords={},
                     viewport={}, request_text="prev")
    prev_cr = repo.create(raw, conversation_id=conv.id)
    from orchestrator.states import State
    prev_cr.state = State.FAILED.value
    db.commit()

    resp = client.post(
        f"/conversations/{conv.id}/messages", json={"text": "怎么修"},
    )
    assert resp.status_code == 200
    assert len(classifier.classify_calls) == 1
    assert classifier.classify_calls[0]["last_cr_state"] == State.FAILED.value


# ── override_mode：业务员 force ────────────────────────────────────


def test_post_messages_override_mode_bypasses_classifier(client):
    """body.override_mode='new_cr' → 直接走 new_cr，跳过 LLM 分类。"""
    classifier, _ = _inject_test_factories(intent_mode="chat_only")
    from orchestrator.main import app_state
    from orchestrator.conversation import ConversationRepository
    db = app_state.session_factory()
    conv = ConversationRepository(db).create()

    resp = client.post(
        f"/conversations/{conv.id}/messages",
        json={"text": "强制 new", "override_mode": "new_cr"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "new_cr"
    assert classifier.classify_calls[0].get("override") == "new_cr"


def test_post_messages_rejects_more_than_3_attachments(client):
    _inject_test_factories(intent_mode="new_cr")
    from orchestrator.main import app_state
    from orchestrator.conversation import ConversationRepository
    db = app_state.session_factory()
    conv = ConversationRepository(db).create()

    resp = client.post(
        f"/conversations/{conv.id}/messages",
        json={
            "text": "?",
            "attachments": [
                {"kind": "framed_region", "mime": "image/png", "b64": f"{i}"}
                for i in range(4)
            ],
        },
    )
    assert resp.status_code == 422
