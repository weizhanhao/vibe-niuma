"""Plan 10 Task 4: chat_responder (chat_only 路径) 测试。

业务员说「你觉得这次改的怎么样？」走 chat_only：
- AI 纯文字回复（不写代码、不承诺改东西）
- append 一条 type=ai message 到 conversation.messages
- 不进 quota / 不切 branch / 不起 docker
- prompt 含 chat history + repo doc（约束 AI 答业务问题）
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.chat_responder import ChatResponder
from orchestrator.conversation import ConversationRepository
from orchestrator.db import Base
import orchestrator.models  # noqa: F401  注册模型


@pytest.fixture
def sqlite_session():
    """单测用 SQLite 内存库 + 单 session。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)
    session = SessionMaker()
    yield session
    session.close()


class _FakeLLM:
    def __init__(self, response: str = "我觉得改得不错"):
        self.response = response
        self.prompts: list[str] = []
        self.call_count = 0

    async def complete(self, prompt: str, *, model: str | None = None) -> str:
        self.call_count += 1
        self.prompts.append(prompt)
        return self.response


@pytest.mark.asyncio
async def test_respond_appends_ai_message_to_conversation(sqlite_session):
    conv_repo = ConversationRepository(sqlite_session)
    conv = conv_repo.create(title="测试")
    conv_repo.append_message(conv.id, {
        "type": "user", "ts": "t0", "content": "改红",
    })

    llm = _FakeLLM("看起来挺好")
    responder = ChatResponder(llm=llm, conversation_repo=conv_repo)
    await responder.respond(
        conversation_id=conv.id,
        user_message="改得怎么样？",
        repo_doc="React 项目，订单管理",
    )

    fresh = conv_repo.get(conv.id)
    ai_messages = [m for m in fresh.messages if m.get("type") == "ai"]
    assert len(ai_messages) == 1
    assert ai_messages[0]["content"] == "看起来挺好"


@pytest.mark.asyncio
async def test_respond_calls_llm_once(sqlite_session):
    conv_repo = ConversationRepository(sqlite_session)
    conv = conv_repo.create(title="x")
    llm = _FakeLLM()
    responder = ChatResponder(llm=llm, conversation_repo=conv_repo)
    await responder.respond(conversation_id=conv.id, user_message="hi", repo_doc="")
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_respond_prompt_includes_chat_history(sqlite_session):
    conv_repo = ConversationRepository(sqlite_session)
    conv = conv_repo.create(title="x")
    conv_repo.append_message(conv.id, {"type": "user", "ts": "t0", "content": "改红"})
    conv_repo.append_message(conv.id, {"type": "ai", "ts": "t1", "content": "改完了"})

    llm = _FakeLLM()
    responder = ChatResponder(llm=llm, conversation_repo=conv_repo)
    await responder.respond(conversation_id=conv.id, user_message="改得怎么样？", repo_doc="")

    p = llm.prompts[0]
    assert "改红" in p
    assert "改完了" in p
    assert "改得怎么样" in p


@pytest.mark.asyncio
async def test_respond_prompt_includes_repo_doc(sqlite_session):
    conv_repo = ConversationRepository(sqlite_session)
    conv = conv_repo.create(title="x")
    llm = _FakeLLM()
    responder = ChatResponder(llm=llm, conversation_repo=conv_repo)
    await responder.respond(
        conversation_id=conv.id,
        user_message="为啥用 React？",
        repo_doc="React + Vite + TypeScript 项目，订单管理后台",
    )
    assert "React" in llm.prompts[0]
    assert "订单" in llm.prompts[0]


@pytest.mark.asyncio
async def test_respond_prompt_constrains_no_code_no_promise(sqlite_session):
    """prompt 必须明令禁止 AI 写代码 / 承诺改东西，避免业务员误以为 AI 真去改了。"""
    conv_repo = ConversationRepository(sqlite_session)
    conv = conv_repo.create(title="x")
    llm = _FakeLLM()
    responder = ChatResponder(llm=llm, conversation_repo=conv_repo)
    await responder.respond(conversation_id=conv.id, user_message="改一下吧", repo_doc="")
    p = llm.prompts[0]
    assert "不要写代码" in p or "不要承诺" in p or "讨论" in p


@pytest.mark.asyncio
async def test_respond_returns_ai_reply_text(sqlite_session):
    conv_repo = ConversationRepository(sqlite_session)
    conv = conv_repo.create(title="x")
    llm = _FakeLLM("这是个好问题")
    responder = ChatResponder(llm=llm, conversation_repo=conv_repo)
    reply = await responder.respond(conversation_id=conv.id, user_message="?", repo_doc="")
    assert reply == "这是个好问题"


@pytest.mark.asyncio
async def test_respond_does_not_create_change_request(sqlite_session):
    """chat_only 路径绝对不应该创建 ChangeRequest 行。"""
    from orchestrator.models import ChangeRequest

    conv_repo = ConversationRepository(sqlite_session)
    conv = conv_repo.create(title="x")
    llm = _FakeLLM()
    responder = ChatResponder(llm=llm, conversation_repo=conv_repo)
    await responder.respond(conversation_id=conv.id, user_message="hi", repo_doc="")

    cr_count = sqlite_session.query(ChangeRequest).count()
    assert cr_count == 0


@pytest.mark.asyncio
async def test_respond_on_unknown_conversation_raises(sqlite_session):
    conv_repo = ConversationRepository(sqlite_session)
    llm = _FakeLLM()
    responder = ChatResponder(llm=llm, conversation_repo=conv_repo)
    with pytest.raises(KeyError):
        await responder.respond(conversation_id="not_exists", user_message="?", repo_doc="")
