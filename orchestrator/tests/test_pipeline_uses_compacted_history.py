"""Plan 9 Task 5: pipeline 调用 dev_runner 之前注入压缩后的 chat history。

设计要点（spec §Task 5）：
- 把 conversation.messages 经 compact() 后塞进 DevContext.chat_history
- 阈值未到 → 原样塞（含老消息全文）
- 阈值到 → 注入 summary message；并把新 summary 持久化到 conversation 表，
  下次 round 直接命中、不重算
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
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={},
        viewport={},
        request_text="把保存按钮改成蓝色",
    )


class _CtxCapturingDevRunner(FakeDevRunner):
    """FakeDevRunner 的子类：把收到的 ctx 存下来，事后断言。"""

    def __init__(self):
        super().__init__()
        self.last_ctx: DevContext | None = None

    async def run(self, repo_path: str, branch: str, ctx: DevContext) -> RunResult:
        self.last_ctx = ctx
        return await super().run(repo_path, branch, ctx)


class _FakeCompactionLLM:
    """注入式 LLM：永远返回 SUMMARY_TEXT；记录被调几次。"""

    SUMMARY_TEXT = "（压缩摘要：老消息已合并）"

    def __init__(self):
        self.calls = 0

    async def summarize(self, prompt: str) -> str:
        self.calls += 1
        return self.SUMMARY_TEXT


def _make_pipeline(
    temp_repo,
    db_session,
    *,
    dev=None,
    compaction_llm=None,
    compaction_threshold_soft: int = 40_000,
):
    return Pipeline(
        repo_path=str(temp_repo),
        repository=ChangeRequestRepository(db_session),
        git_manager=GitManager(str(temp_repo)),
        event_bus=EventBus(),
        quota=QuotaManager(capacity=5),
        interaction_skill=FakeInteractionSkill(question_count=0),
        stack_adapter=FakeStackAdapter(),
        dev_runner=dev or FakeDevRunner(),
        preview_adapter=FakePreviewAdapter(),
        compaction_llm=compaction_llm,
        compaction_threshold_soft=compaction_threshold_soft,
    )


async def test_dev_runner_receives_raw_when_under_threshold(temp_repo, db_session):
    """阈值未到（默认 40k）：chat_history 直接是 conversation 原文。"""
    conv_repo = ConversationRepository(db_session)
    conv = conv_repo.create(title="按钮颜色")
    conv_repo.append_message(conv.id, {"type": "user", "ts": "t0", "content": "改红色"})
    conv_repo.append_message(conv.id, {"type": "ai", "ts": "t1", "content": "好的，已改"})

    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw(), conversation_id=conv.id)
    runner = _CtxCapturingDevRunner()
    fake_llm = _FakeCompactionLLM()
    pipeline = _make_pipeline(
        temp_repo, db_session, dev=runner, compaction_llm=fake_llm,
    )

    await pipeline.run(cr.id)

    assert runner.last_ctx is not None
    assert len(runner.last_ctx.chat_history) == 2
    contents = [m["content"] for m in runner.last_ctx.chat_history]
    assert "改红色" in contents
    assert "好的，已改" in contents
    assert fake_llm.calls == 0


async def test_dev_runner_receives_compacted_messages_when_over_threshold(
    temp_repo, db_session,
):
    """阈值触发：chat_history 含 summary message；老 user 消息仍在。"""
    conv_repo = ConversationRepository(db_session)
    conv = conv_repo.create(title="多轮")
    for i in range(7):
        conv_repo.append_message(conv.id, {"type": "user", "ts": f"u{i}", "content": f"需求{i}"})
        conv_repo.append_message(conv.id, {"type": "ai", "ts": f"a{i}", "content": f"ai-resp-{i}"})

    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw(), conversation_id=conv.id)
    runner = _CtxCapturingDevRunner()
    fake_llm = _FakeCompactionLLM()
    pipeline = _make_pipeline(
        temp_repo, db_session, dev=runner, compaction_llm=fake_llm,
        compaction_threshold_soft=1,
    )

    await pipeline.run(cr.id)

    assert runner.last_ctx is not None
    types = [m["type"] for m in runner.last_ctx.chat_history]
    assert "summary" in types
    contents = [m["content"] for m in runner.last_ctx.chat_history]
    assert "需求0" in contents
    assert _FakeCompactionLLM.SUMMARY_TEXT in contents
    assert fake_llm.calls == 1


async def test_summary_persisted_to_conversation(temp_repo, db_session):
    """compaction 产生的新 summary 应该 append 到 conversation.messages，
    下次同 conversation 再起 CR 时直接复用，不再重算 LLM。"""
    conv_repo = ConversationRepository(db_session)
    conv = conv_repo.create(title="多轮")
    for i in range(7):
        conv_repo.append_message(conv.id, {"type": "user", "ts": f"u{i}", "content": f"需求{i}"})
        conv_repo.append_message(conv.id, {"type": "ai", "ts": f"a{i}", "content": f"ai-resp-{i}"})
    pre = sum(1 for m in conv_repo.get(conv.id).messages if m.get("type") == "summary")

    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw(), conversation_id=conv.id)
    fake_llm = _FakeCompactionLLM()
    pipeline = _make_pipeline(
        temp_repo, db_session, compaction_llm=fake_llm, compaction_threshold_soft=1,
    )

    await pipeline.run(cr.id)

    fresh = conv_repo.get(conv.id)
    post = sum(1 for m in fresh.messages if m.get("type") == "summary")
    assert post == pre + 1
    summary_contents = [
        m["content"] for m in fresh.messages if m.get("type") == "summary"
    ]
    assert _FakeCompactionLLM.SUMMARY_TEXT in summary_contents


async def test_no_conversation_id_skips_compaction(temp_repo, db_session):
    """没挂 conversation 的 CR（兼容老 flow）—— chat_history 空，LLM 不调。"""
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw(), conversation_id=None)
    runner = _CtxCapturingDevRunner()
    fake_llm = _FakeCompactionLLM()
    pipeline = _make_pipeline(
        temp_repo, db_session, dev=runner, compaction_llm=fake_llm,
        compaction_threshold_soft=1,
    )

    await pipeline.run(cr.id)

    assert runner.last_ctx is not None
    assert runner.last_ctx.chat_history == []
    assert fake_llm.calls == 0


async def test_happy_path_still_reaches_preview_ready_with_compaction(
    temp_repo, db_session,
):
    """sanity: 加 compaction 不破 happy path 状态转移。"""
    conv_repo = ConversationRepository(db_session)
    conv = conv_repo.create(title="happy")
    conv_repo.append_message(conv.id, {"type": "user", "ts": "t0", "content": "改"})

    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw(), conversation_id=conv.id)
    pipeline = _make_pipeline(temp_repo, db_session, compaction_llm=_FakeCompactionLLM())

    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.PREVIEW_READY.value
