from datetime import datetime, timedelta

from orchestrator.adapters.fakes import FakePreviewAdapter
from orchestrator.adapters.types import RawRequest
from orchestrator.quota import QuotaManager
from orchestrator.reaper import reap_idle_previews, reap_orphan_previews
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


# ── orphan preview cleanup（终态 CR 容器还活着）────────────────────


async def test_reap_orphan_tears_down_failed_cr_with_preview(db_session):
    """pipeline 失败但 preview_handle 还在 → orphan reaper 拆容器 + 清指针。"""
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    # 推到 BUILDING，set_preview，然后 mark_failed —— 模拟「build 失败但
    # 已起 preview 容器」的真实失败路径
    for s in (State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING):
        repo.transition(cr.id, s)
    repo.set_preview(cr.id, url="http://x:5108", handle="orphan_h")
    repo.mark_failed(cr.id, phase="building", reason="vite-error", log="...")

    preview = FakePreviewAdapter()
    preview.live_handles.add("orphan_h")

    cleaned = await reap_orphan_previews(repository=repo, preview_adapter=preview)

    assert cr.id in cleaned
    assert "orphan_h" not in preview.live_handles
    # preview_handle 已清，下次扫不会重复拆
    fresh = repo.get(cr.id)
    assert fresh.preview_handle is None
    assert fresh.preview_url is None
    # state 保持 FAILED（不变到 EXPIRED），因为业务员可能想看失败原因
    assert fresh.state == State.FAILED.value


async def test_reap_orphan_skips_active_cr(db_session):
    """非终态 CR 即使有 preview_handle 也不清。"""
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    _drive_to_preview_ready(repo, cr.id)  # 推到 PREVIEW_READY（非终态）
    repo.set_preview(cr.id, url="http://x:5101", handle="active_h")

    preview = FakePreviewAdapter()
    preview.live_handles.add("active_h")

    cleaned = await reap_orphan_previews(repository=repo, preview_adapter=preview)

    assert cleaned == []
    assert "active_h" in preview.live_handles
    assert repo.get(cr.id).preview_handle == "active_h"


async def test_reap_orphan_handles_teardown_failure_gracefully(db_session):
    """preview.teardown 抛异常也得清 DB 指针 —— 否则下次扫会再 try（无限重试）。"""
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    for s in (State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING):
        repo.transition(cr.id, s)
    repo.set_preview(cr.id, url="http://x:5108", handle="explode_h")
    repo.mark_failed(cr.id, phase="building", reason="x", log="")

    class _ExplodingPreview(FakePreviewAdapter):
        async def teardown(self, instance):  # type: ignore[override]
            raise RuntimeError("docker daemon down")

    preview = _ExplodingPreview()
    cleaned = await reap_orphan_previews(repository=repo, preview_adapter=preview)

    # 即使 teardown 失败，DB 指针仍被清掉（best-effort）
    assert cr.id in cleaned
    assert repo.get(cr.id).preview_handle is None
