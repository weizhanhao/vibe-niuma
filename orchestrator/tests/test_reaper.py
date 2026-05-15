from datetime import datetime, timedelta

from orchestrator.adapters.fakes import FakePreviewAdapter
from orchestrator.adapters.types import RawRequest
from orchestrator.quota import QuotaManager
from orchestrator.reaper import reap_idle_previews
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


def _raw():
    return RawRequest(
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={},
        viewport={},
        request_text="x",
    )


def _drive_to_preview_ready(repo, cr_id):
    for s in (
        State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING, State.PREVIEW_READY
    ):
        repo.transition(cr_id, s)


async def test_reap_marks_stale_preview_ready_as_expired(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    _drive_to_preview_ready(repo, cr.id)
    repo.set_preview(cr.id, url="http://x:5101", handle="h1")
    # 把 last_activity 拨到 2 小时前
    obj = repo.get(cr.id)
    obj.last_activity_at = datetime.utcnow() - timedelta(hours=2)
    db_session.commit()

    quota = QuotaManager(capacity=5)
    await quota.acquire(cr.id)
    preview = FakePreviewAdapter()
    preview.live_handles.add("h1")

    reaped = await reap_idle_previews(
        repository=repo, quota=quota, preview_adapter=preview, ttl_seconds=3600
    )

    assert cr.id in reaped
    assert repo.get(cr.id).state == State.EXPIRED.value
    assert quota.in_use() == 0  # 槽位被释放
    assert "h1" not in preview.live_handles  # 预览实例被拆


async def test_reap_leaves_fresh_preview_ready_alone(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    _drive_to_preview_ready(repo, cr.id)
    repo.set_preview(cr.id, url="http://x:5101", handle="h2")

    quota = QuotaManager(capacity=5)
    await quota.acquire(cr.id)
    preview = FakePreviewAdapter()
    preview.live_handles.add("h2")

    reaped = await reap_idle_previews(
        repository=repo, quota=quota, preview_adapter=preview, ttl_seconds=3600
    )

    assert cr.id not in reaped
    assert repo.get(cr.id).state == State.PREVIEW_READY.value
    assert quota.in_use() == 1
