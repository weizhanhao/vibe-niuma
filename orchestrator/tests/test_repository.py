import pytest

from orchestrator.adapters.types import RawRequest
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


def _raw(text="改个颜色") -> RawRequest:
    return RawRequest(
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={"x": 1},
        viewport={"width": 1280},
        request_text=text,
    )


def test_create_returns_record_in_created_state(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    assert cr.id
    assert cr.state == State.CREATED.value
    assert cr.request_text == "改个颜色"
    assert cr.branch is None


def test_get_returns_persisted_record(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw("找回它"))
    fetched = repo.get(cr.id)
    assert fetched is not None
    assert fetched.request_text == "找回它"


def test_get_missing_returns_none(db_session):
    repo = ChangeRequestRepository(db_session)
    assert repo.get("nonexistent") is None


def test_transition_state_valid(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    repo.transition(cr.id, State.CLARIFYING)
    assert repo.get(cr.id).state == State.CLARIFYING.value


def test_transition_state_invalid_raises(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    with pytest.raises(ValueError):
        repo.transition(cr.id, State.PREVIEW_READY)  # created → preview-ready 非法


def test_mark_failed_records_phase_reason_log(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    repo.transition(cr.id, State.CLARIFYING)
    repo.mark_failed(cr.id, phase="coding", reason="crash", log="traceback...")
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "coding"
    assert fetched.fail_reason == "crash"
    assert fetched.fail_log == "traceback..."


def test_set_branch_and_preview(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    repo.set_branch(cr.id, "cr/abc")
    repo.set_preview(cr.id, url="http://x:5101", handle="container-1")
    fetched = repo.get(cr.id)
    assert fetched.branch == "cr/abc"
    assert fetched.preview_url == "http://x:5101"
    assert fetched.preview_handle == "container-1"


def test_touch_activity_updates_last_activity(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    before = repo.get(cr.id).last_activity_at
    repo.touch_activity(cr.id)
    assert repo.get(cr.id).last_activity_at >= before


def test_list_non_terminal_and_stale(db_session):
    from datetime import datetime, timedelta

    repo = ChangeRequestRepository(db_session)
    active = repo.create(_raw("active"))
    repo.transition(active.id, State.CLARIFYING)
    done = repo.create(_raw("done"))
    repo.transition(done.id, State.CLARIFYING)
    repo.transition(done.id, State.DISCARDED)
    stale = repo.create(_raw("stale"))
    for s in (State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING, State.PREVIEW_READY):
        repo.transition(stale.id, s)
    obj = repo.get(stale.id)
    obj.last_activity_at = datetime.utcnow() - timedelta(hours=2)
    db_session.commit()

    non_terminal_ids = {c.id for c in repo.list_non_terminal()}
    assert active.id in non_terminal_ids
    assert stale.id in non_terminal_ids
    assert done.id not in non_terminal_ids

    stale_ids = {c.id for c in repo.list_stale_previews(older_than_seconds=3600)}
    assert stale.id in stale_ids
    assert active.id not in stale_ids
