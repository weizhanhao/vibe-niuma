import asyncio
import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.fakes import (
    FakeDevRunner,
    FakeInteractionSkill,
    FakePreviewAdapter,
    FakeStackAdapter,
)
from orchestrator.adapters.types import RawRequest
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


def _make_pipeline(
    temp_repo,
    db_session,
    *,
    interaction=None,
    stack=None,
    dev=None,
    preview=None,
    quota=None,
):
    return Pipeline(
        repo_path=str(temp_repo),
        repository=ChangeRequestRepository(db_session),
        git_manager=GitManager(str(temp_repo)),
        event_bus=EventBus(),
        quota=quota or QuotaManager(capacity=5),
        interaction_skill=interaction or FakeInteractionSkill(question_count=0),
        stack_adapter=stack or FakeStackAdapter(),
        dev_runner=dev or FakeDevRunner(),
        preview_adapter=preview or FakePreviewAdapter(),
    )


async def test_happy_path_reaches_preview_ready(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(temp_repo, db_session)
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.PREVIEW_READY.value
    assert fetched.branch == f"cr/{cr.id}"
    assert fetched.preview_url is not None
    assert fetched.preview_handle is not None


async def test_locate_failure_fails_at_located(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, stack=FakeStackAdapter(locate_succeeds=False)
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "located"


async def test_dev_runner_crash_fails_at_coding(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(temp_repo, db_session, dev=FakeDevRunner(raises=True))
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "coding"


async def test_dev_runner_no_changes_fails_at_coding(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, dev=FakeDevRunner(produces_changes=False)
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "coding"
    assert fetched.fail_reason == "no-changes"


async def test_build_failure_fails_at_building(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, stack=FakeStackAdapter(build_succeeds=False)
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "building"


async def test_preview_serve_failure_fails_at_building(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, preview=FakePreviewAdapter(serve_succeeds=False)
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "building"


async def test_scripted_clarification_runs_interactive_dance(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, interaction=FakeInteractionSkill(question_count=2)
    )
    # 在后台跑 pipeline；它会在 clarifying 阶段挂起等回答
    task = asyncio.create_task(pipeline.run(cr.id))
    await asyncio.sleep(0.05)
    assert repo.get(cr.id).state == State.CLARIFYING.value
    # 回答两个澄清问题
    channel = pipeline.channel_for(cr.id)
    sub = pipeline.event_bus.subscribe(cr.id)
    answered = 0
    while answered < 2:
        evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
        if evt.type == "question":
            channel.submit_answer(evt.data["question_id"], "我的回答")
            answered += 1
    await asyncio.wait_for(task, timeout=2)
    assert repo.get(cr.id).state == State.PREVIEW_READY.value


async def test_quota_slot_held_at_preview_ready_released_on_failure(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    quota = QuotaManager(capacity=5)
    # happy path 到 preview-ready（非终态）—— 槽位仍被占用
    cr = repo.create(_raw())
    await _make_pipeline(temp_repo, db_session, quota=quota).run(cr.id)
    assert quota.in_use() == 1
    # 失败路径会释放槽位
    cr2 = repo.create(_raw())
    await _make_pipeline(
        temp_repo, db_session, quota=quota, dev=FakeDevRunner(raises=True)
    ).run(cr2.id)
    assert quota.in_use() == 1  # cr2 失败已释放，只剩 cr1
