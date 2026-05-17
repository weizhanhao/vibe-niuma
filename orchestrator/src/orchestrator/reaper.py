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


async def reap_orphan_previews(
    repository: ChangeRequestRepository,
    preview_adapter: PreviewAdapter,
) -> list[str]:
    """清理终态（failed/merged/discarded/expired）但 preview_handle 还在的容器。

    pipeline 失败路径历史上不拆 preview，accumulates 13+ 个孤儿容器把 5100~5199
    端口段占满。每 reaper 周期扫一次终态 CR 兜底；不等 idle TTL，因为终态 CR
    不可能再被业务员合并，留容器就是纯浪费。

    返回被清理掉的 request id 列表。
    """
    cleaned: list[str] = []
    for cr in repository.list_orphan_previews():
        handle = cr.preview_handle
        if not handle:
            continue
        instance = PreviewInstance(
            preview_id="", url=cr.preview_url or "", handle=handle
        )
        try:
            await preview_adapter.teardown(instance)
        except Exception:  # noqa: BLE001  best-effort
            pass
        try:
            repository.clear_preview(cr.id)
        except Exception:  # noqa: BLE001
            pass
        cleaned.append(cr.id)
    return cleaned


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
                repo = ChangeRequestRepository(db)
                await reap_idle_previews(
                    repository=repo,
                    quota=quota,
                    preview_adapter=preview_adapter,
                    ttl_seconds=ttl_seconds,
                )
                # 兜底清孤儿：终态 CR 但容器还在的（pipeline 失败路径常见）
                await reap_orphan_previews(
                    repository=repo,
                    preview_adapter=preview_adapter,
                )
            finally:
                db.close()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001  单次扫描失败不应杀死循环
            continue
