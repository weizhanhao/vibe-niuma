"""IdleReaper —— 把闲置超时的 preview-ready 请求标 expired、拆预览、释放配额。

`reap_idle_previews` 是一次扫描（可测试）；`run_reaper_loop` 是 lifespan 里跑的后台循环。
"""
import asyncio

from orchestrator.adapters.interfaces import PreviewAdapter
from orchestrator.adapters.types import PreviewInstance
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


async def reap_idle_previews(
    repository: ChangeRequestRepository,
    quota: QuotaManager,
    preview_adapter: PreviewAdapter,
    ttl_seconds: int,
) -> list[str]:
    """扫一遍 stale 的 preview-ready 请求，逐个 expire。返回被回收的 request id 列表。"""
    reaped: list[str] = []
    for cr in repository.list_stale_previews(older_than_seconds=ttl_seconds):
        if cr.preview_handle:
            instance = PreviewInstance(
                preview_id="", url=cr.preview_url or "", handle=cr.preview_handle
            )
            try:
                await preview_adapter.teardown(instance)
            except Exception:  # noqa: BLE001  best-effort 清理
                pass
        repository.transition(cr.id, State.EXPIRED)
        quota.release(cr.id)
        reaped.append(cr.id)
    return reaped


async def run_reaper_loop(
    session_factory,
    quota: QuotaManager,
    preview_adapter: PreviewAdapter,
    ttl_seconds: int,
    interval_seconds: int,
) -> None:
    """后台循环：每 interval 秒扫一次。lifespan 启动它，取消时干净退出。"""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            db = session_factory()
            try:
                await reap_idle_previews(
                    repository=ChangeRequestRepository(db),
                    quota=quota,
                    preview_adapter=preview_adapter,
                    ttl_seconds=ttl_seconds,
                )
            finally:
                db.close()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001  单次扫描失败不应杀死循环
            continue
