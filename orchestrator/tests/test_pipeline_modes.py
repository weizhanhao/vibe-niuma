"""Plan 10 Task 5: pipeline.run(mode) 三分支测试。

业务员视角：
  - 第一次提需求「订单徽章改红」→ mode='new_cr' → 完整 pipeline
  - 接着说「字号大一点」→ mode='refine_cr' → 复用上 CR 的 branch + preview
  - 接着说「为啥用这种方案」→ mode='chat_only' → 纯 LLM 回复，不产生新 CR
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.fakes import (
    FakeDevRunner,
    FakeInteractionSkill,
    FakePreviewAdapter,
    FakeStackAdapter,
)
from orchestrator.adapters.types import DevContext, RawRequest, RunResult
from orchestrator.chat_responder import ChatResponder
from orchestrator.conversation import ConversationRepository
from orchestrator.events import EventBus
from orchestrator.git_manager import GitManager
from orchestrator.pipeline import Pipeline
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


@pytest.fixture
def temp_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "test@test")
    run("config", "user.name", "test")
    (repo / "file.txt").write_text("v1\n")
    run("add", "file.txt")
    run("commit", "-m", "init")
    return repo


def _raw() -> RawRequest:
    return RawRequest(
        url="http://x/orders", screenshot_b64="img",
        box_coords={}, viewport={}, request_text="改",
    )


class _CtxCapturingDevRunner(FakeDevRunner):
    def __init__(self):
        super().__init__()
        self.last_ctx: DevContext | None = None
        self.call_count = 0

    async def run(self, repo_path: str, branch: str, ctx: DevContext) -> RunResult:
        self.call_count += 1
        self.last_ctx = ctx
        return await super().run(repo_path, branch, ctx)


class _FakeChatLLM:
    def __init__(self, response: str = "我觉得改得不错"):
        self.response = response
        self.call_count = 0

    async def complete(self, prompt: str, *, model: str | None = None) -> str:
        self.call_count += 1
        return self.response


def _make_pipeline(temp_repo, db_session, *, dev=None, chat_responder=None):
    interaction = FakeInteractionSkill(question_count=0)
    return Pipeline(
        repo_path=str(temp_repo),
        repository=ChangeRequestRepository(db_session),
        git_manager=GitManager(str(temp_repo)),
        event_bus=EventBus(),
        quota=QuotaManager(capacity=5),
        interaction_skill=interaction,
        stack_adapter=FakeStackAdapter(),
        dev_runner=dev or FakeDevRunner(),
        preview_adapter=FakePreviewAdapter(),
        chat_responder=chat_responder,
    )


# ── mode='new_cr' (现状不破) ────────────────────────────────────────


async def test_mode_new_cr_runs_full_pipeline_default(temp_repo, db_session):
    """mode 缺省 = 'new_cr'：完整 pipeline 到 preview-ready。"""
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(temp_repo, db_session)
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.PREVIEW_READY.value


async def test_mode_new_cr_explicit(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(temp_repo, db_session)
    await pipeline.run(cr.id, mode="new_cr")
    fetched = repo.get(cr.id)
    assert fetched.state == State.PREVIEW_READY.value


# ── mode='refine_cr' ────────────────────────────────────────────────


async def test_mode_refine_cr_skips_clarify_located_directly_to_coding(
    temp_repo, db_session,
):
    """refine 跳过 clarifying / locating，dev_runner 直接被调一次。"""
    repo = ChangeRequestRepository(db_session)
    base_cr = repo.create(_raw())
    base_pipeline = _make_pipeline(temp_repo, db_session)
    await base_pipeline.run(base_cr.id)
    base = repo.get(base_cr.id)
    assert base.state == State.PREVIEW_READY.value
    base_branch = base.branch

    refine_cr = repo.create(_raw())
    repo.set_refine_of(refine_cr.id, base_cr.id)
    runner = _CtxCapturingDevRunner()
    pipeline = _make_pipeline(temp_repo, db_session, dev=runner)

    await pipeline.run(refine_cr.id, mode="refine_cr")

    assert runner.call_count == 1
    assert runner.last_ctx is not None
    assert getattr(runner.last_ctx, "mode", None) == "refine"
    assert getattr(runner.last_ctx, "base_branch", None) == base_branch


async def test_mode_refine_cr_reuses_base_preview_url(temp_repo, db_session):
    """refine 完成后 preview_url 直接复用上 CR 的（容器热重载，不重起）。"""
    repo = ChangeRequestRepository(db_session)
    base_cr = repo.create(_raw())
    base_pipeline = _make_pipeline(temp_repo, db_session)
    await base_pipeline.run(base_cr.id)
    base = repo.get(base_cr.id)
    base_url = base.preview_url
    base_handle = base.preview_handle

    refine_cr = repo.create(_raw())
    repo.set_refine_of(refine_cr.id, base_cr.id)
    pipeline = _make_pipeline(temp_repo, db_session)
    await pipeline.run(refine_cr.id, mode="refine_cr")

    fetched = repo.get(refine_cr.id)
    assert fetched.state == State.PREVIEW_READY.value
    assert fetched.preview_url == base_url
    assert fetched.preview_handle == base_handle


async def test_mode_refine_cr_reaches_preview_ready_state(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    base_cr = repo.create(_raw())
    base_pipeline = _make_pipeline(temp_repo, db_session)
    await base_pipeline.run(base_cr.id)

    refine_cr = repo.create(_raw())
    repo.set_refine_of(refine_cr.id, base_cr.id)
    pipeline = _make_pipeline(temp_repo, db_session)
    await pipeline.run(refine_cr.id, mode="refine_cr")
    fetched = repo.get(refine_cr.id)
    assert fetched.state == State.PREVIEW_READY.value


async def test_mode_refine_cr_without_refine_of_marks_failed(temp_repo, db_session):
    """refine 必须有 refine_of；缺则 mark_failed 而非 raise（保持错误路径一致）。"""
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(temp_repo, db_session)
    await pipeline.run(cr.id, mode="refine_cr")
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase in ("refine", "coding")


# ── mode='chat_only' via run_chat_only() ──────────────────────────


async def test_run_chat_only_calls_chat_responder_appends_message(temp_repo, db_session):
    conv_repo = ConversationRepository(db_session)
    conv = conv_repo.create(title="chat 测")
    chat_llm = _FakeChatLLM("我觉得改得不错")
    chat_resp = ChatResponder(llm=chat_llm, conversation_repo=conv_repo)
    pipeline = _make_pipeline(temp_repo, db_session, chat_responder=chat_resp)

    reply = await pipeline.run_chat_only(
        conversation_id=conv.id,
        user_message="改得怎么样？",
        repo_doc="React 项目",
    )

    assert reply == "我觉得改得不错"
    assert chat_llm.call_count == 1
    fresh = conv_repo.get(conv.id)
    ai_msgs = [m for m in fresh.messages if m.get("type") == "ai"]
    assert len(ai_msgs) == 1
    assert ai_msgs[0]["content"] == "我觉得改得不错"


async def test_run_chat_only_does_not_create_change_request(temp_repo, db_session):
    """chat_only 绝不产生 CR 行。"""
    from orchestrator.models import ChangeRequest

    conv_repo = ConversationRepository(db_session)
    conv = conv_repo.create(title="x")
    chat_resp = ChatResponder(llm=_FakeChatLLM(), conversation_repo=conv_repo)
    pipeline = _make_pipeline(temp_repo, db_session, chat_responder=chat_resp)

    before = db_session.query(ChangeRequest).count()
    await pipeline.run_chat_only(
        conversation_id=conv.id, user_message="hi", repo_doc="",
    )
    after = db_session.query(ChangeRequest).count()
    assert before == after


async def test_run_chat_only_without_chat_responder_raises(temp_repo, db_session):
    """没注入 chat_responder 时调 run_chat_only → 报错（防误用）。"""
    pipeline = _make_pipeline(temp_repo, db_session, chat_responder=None)
    conv_repo = ConversationRepository(db_session)
    conv = conv_repo.create(title="x")
    with pytest.raises(Exception):
        await pipeline.run_chat_only(
            conversation_id=conv.id, user_message="hi", repo_doc="",
        )
