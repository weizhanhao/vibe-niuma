"""Self-heal v2: Pipeline.run_self_heal() —— 浏览器侧 runtime 错误 → 同 branch
让 dev_runner 续改一轮 + refresh 进容器。

跟 test_self_heal.py（SelfHealClassifier，构建失败分类器）不重叠：本文件覆盖
Pipeline 入口本身、ctx.mode/base_branch 串接、refresh 容器、异常吞掉等行为。
"""
from __future__ import annotations

import pytest

from orchestrator.adapters.fakes import (
    FakeDevRunner, FakeInteractionSkill, FakePreviewAdapter, FakeStackAdapter,
)
from orchestrator.adapters.types import RawRequest
from orchestrator.events import EventBus
from orchestrator.git_manager import GitManager
from orchestrator.pipeline import Pipeline, _format_self_heal_errors
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


def _make_pipeline(repo_path: str, db, preview: FakePreviewAdapter, dev: FakeDevRunner):
    return Pipeline(
        repo_path=repo_path,
        repository=ChangeRequestRepository(db),
        git_manager=GitManager(repo_path),
        event_bus=EventBus(),
        quota=QuotaManager(capacity=5),
        interaction_skill=FakeInteractionSkill(question_count=0),
        stack_adapter=FakeStackAdapter(),
        dev_runner=dev,
        preview_adapter=preview,
    )


def _make_preview_ready_cr(db, *, branch: str = "cr/self-heal-1", handle: str = "fake-handle-1"):
    repo = ChangeRequestRepository(db)
    cr = repo.create(RawRequest(
        url="http://localhost:5173/orders",
        screenshot_b64="",
        box_coords={},
        viewport={},
        request_text="加一列",
    ))
    repo.set_branch(cr.id, branch)
    repo.set_preview(cr.id, url="http://localhost:5101", handle=handle)
    # FSM 不允许 created → preview-ready 直跳，按合法顺序走一遍。
    for st in (
        State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING, State.PREVIEW_READY,
    ):
        repo.transition(cr.id, st)
    return cr


SAMPLE_ERRORS = [
    {
        "message": "Cannot read properties of undefined (reading 'split')",
        "pageUrl": "http://localhost:5101/orders",
        "stack": "at OrderList (OrderList.tsx:42)\nat render",
        "ts": "2026-05-17T10:00:00Z",
    },
]


def test_format_self_heal_errors_includes_message_and_stack():
    out = _format_self_heal_errors(SAMPLE_ERRORS)
    assert "Cannot read properties of undefined" in out
    assert "OrderList.tsx:42" in out
    assert "http://localhost:5101/orders" in out


def test_format_self_heal_errors_empty():
    assert _format_self_heal_errors([]) == "（未捕到具体错误内容）"


@pytest.mark.asyncio
async def test_run_self_heal_invokes_dev_runner_and_refreshes_container(
    db_session, orchestrator_repo,
):
    """happy path：preview-ready CR + 有 branch/handle → dev_runner 跑 + refresh 调用。"""
    preview = FakePreviewAdapter()
    dev = FakeDevRunner(produces_changes=True)
    pipeline = _make_pipeline(str(orchestrator_repo), db_session, preview, dev)
    cr = _make_preview_ready_cr(db_session, handle="fake-handle-happy")

    await pipeline.run_self_heal(cr.id, SAMPLE_ERRORS)

    refreshed = getattr(preview, "refreshed_handles", [])
    assert refreshed == [("fake-handle-happy", str(orchestrator_repo))]

    db_session.expire_all()
    again = ChangeRequestRepository(db_session).get(cr.id)
    assert again.state == State.PREVIEW_READY.value


@pytest.mark.asyncio
async def test_run_self_heal_skips_when_no_branch(db_session, orchestrator_repo):
    """边界：CR 没 branch → 早退，不调 dev_runner。"""
    preview = FakePreviewAdapter()

    class _CountingRunner(FakeDevRunner):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def run(self, repo_path, branch, ctx):
            self.calls += 1
            return await super().run(repo_path, branch, ctx)

    dev = _CountingRunner()
    pipeline = _make_pipeline(str(orchestrator_repo), db_session, preview, dev)

    repo = ChangeRequestRepository(db_session)
    cr = repo.create(RawRequest(
        url="http://x", screenshot_b64="", box_coords={}, viewport={},
        request_text="x",
    ))

    await pipeline.run_self_heal(cr.id, SAMPLE_ERRORS)

    assert dev.calls == 0
    assert not getattr(preview, "refreshed_handles", [])


@pytest.mark.asyncio
async def test_run_self_heal_skips_when_no_preview_handle(
    db_session, orchestrator_repo,
):
    """边界：CR 有 branch 但没 preview_handle（异常状态）→ 早退。"""
    preview = FakePreviewAdapter()

    class _CountingRunner(FakeDevRunner):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def run(self, repo_path, branch, ctx):
            self.calls += 1
            return await super().run(repo_path, branch, ctx)

    dev = _CountingRunner()
    pipeline = _make_pipeline(str(orchestrator_repo), db_session, preview, dev)

    repo = ChangeRequestRepository(db_session)
    cr = repo.create(RawRequest(
        url="http://x", screenshot_b64="", box_coords={}, viewport={},
        request_text="x",
    ))
    repo.set_branch(cr.id, "cr/no-handle")

    await pipeline.run_self_heal(cr.id, SAMPLE_ERRORS)

    assert dev.calls == 0


@pytest.mark.asyncio
async def test_run_self_heal_no_changes_skips_refresh(
    db_session, orchestrator_repo,
):
    """dev_runner 没产出 → 不 refresh 容器。"""
    preview = FakePreviewAdapter()
    dev = FakeDevRunner(produces_changes=False)
    pipeline = _make_pipeline(str(orchestrator_repo), db_session, preview, dev)
    cr = _make_preview_ready_cr(db_session, handle="fake-handle-nc")

    await pipeline.run_self_heal(cr.id, SAMPLE_ERRORS)

    assert not getattr(preview, "refreshed_handles", [])


@pytest.mark.asyncio
async def test_run_self_heal_dev_runner_crash_swallowed(
    db_session, orchestrator_repo,
):
    """dev_runner 抛异常 → run_self_heal 吞掉，不让 CR 进 failed 状态。"""
    preview = FakePreviewAdapter()
    dev = FakeDevRunner(raises=True)
    pipeline = _make_pipeline(str(orchestrator_repo), db_session, preview, dev)
    cr = _make_preview_ready_cr(db_session, handle="fake-handle-crash")

    await pipeline.run_self_heal(cr.id, SAMPLE_ERRORS)

    db_session.expire_all()
    again = ChangeRequestRepository(db_session).get(cr.id)
    assert again.state == State.PREVIEW_READY.value
    assert again.fail_phase is None


@pytest.mark.asyncio
async def test_run_self_heal_passes_refine_mode_to_dev_runner(
    db_session, orchestrator_repo,
):
    """dev_runner 收到的 ctx.mode='refine' + base_branch=cr.branch（同 branch 续改）。"""
    preview = FakePreviewAdapter()

    captured: dict = {}

    class _CapturingRunner(FakeDevRunner):
        async def run(self, repo_path, branch, ctx):
            captured["branch"] = branch
            captured["mode"] = ctx.mode
            captured["base_branch"] = ctx.base_branch
            captured["clarifications"] = list(ctx.brief.clarifications)
            return await super().run(repo_path, branch, ctx)

    dev = _CapturingRunner()
    pipeline = _make_pipeline(str(orchestrator_repo), db_session, preview, dev)
    cr = _make_preview_ready_cr(db_session, branch="cr/refine-target", handle="h")

    await pipeline.run_self_heal(cr.id, SAMPLE_ERRORS)

    assert captured["branch"] == "cr/refine-target"
    assert captured["mode"] == "refine"
    assert captured["base_branch"] == "cr/refine-target"
    assert captured["clarifications"]
    answer = captured["clarifications"][0]["answer"]
    assert "Cannot read properties of undefined" in answer
