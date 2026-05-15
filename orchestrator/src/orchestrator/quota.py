"""QuotaManager —— 限制同时在飞的变更请求数（设计文档把槽位对应到「容器」）。

请求离开 `created` 前 acquire 一个槽位；进入任一终态时 release。release 幂等。

释放一个槽位时，立即把队首等待者标记为已持有（再唤醒它的 future）——
这样紧接着对该等待者 id 的 release 也能正确腾出槽位，不依赖事件循环的调度时机。
"""
import asyncio


class QuotaManager:
    def __init__(self, capacity: int):
        self._capacity = capacity
        self._held: set[str] = set()
        self._waiters: list[tuple[str, asyncio.Future]] = []

    def in_use(self) -> int:
        return len(self._held)

    def waiting(self) -> int:
        return len(self._waiters)

    async def acquire(self, request_id: str) -> None:
        if request_id in self._held:
            return
        if len(self._held) < self._capacity:
            self._held.add(request_id)
            return
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._waiters.append((request_id, fut))
        await fut  # release() 已在唤醒前把本 id 加入 _held

    def release(self, request_id: str) -> None:
        if request_id not in self._held:
            return
        self._held.discard(request_id)
        if self._waiters:
            next_id, fut = self._waiters.pop(0)
            self._held.add(next_id)
            if not fut.done():
                fut.set_result(None)
