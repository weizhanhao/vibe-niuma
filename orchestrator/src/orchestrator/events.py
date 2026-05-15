"""EventBus —— 每个变更请求一个事件队列，供 SSE 端点订阅、Pipeline 发布。

历史事件保留在 buffer 里：晚订阅的客户端（或 SSE 重连）也能拿到此前的状态变迁。
publish 把事件同时写 buffer（供回放）和 queue（供实时）；subscribe 先回放 buffer，
再消费 queue —— 跳过 queue 里与已回放历史等量的旧事件以避免重复。
"""
import asyncio
from dataclasses import dataclass, field


@dataclass
class Event:
    type: str  # "status" | "question" | "variants"
    data: dict


_SENTINEL = object()


@dataclass
class _Channel:
    buffer: list[Event] = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    closed: bool = False


class EventBus:
    def __init__(self) -> None:
        self._channels: dict[str, _Channel] = {}

    def _channel(self, request_id: str) -> _Channel:
        if request_id not in self._channels:
            self._channels[request_id] = _Channel()
        return self._channels[request_id]

    async def publish(self, request_id: str, event: Event) -> None:
        ch = self._channel(request_id)
        ch.buffer.append(event)
        await ch.queue.put(event)

    def close(self, request_id: str) -> None:
        ch = self._channel(request_id)
        ch.closed = True
        ch.queue.put_nowait(_SENTINEL)

    async def subscribe(self, request_id: str):
        """异步生成器：先回放 buffer 里的历史事件，再实时产出后续新事件。"""
        ch = self._channel(request_id)
        replayed = len(ch.buffer)
        for evt in list(ch.buffer):
            yield evt
        # queue 里前 `replayed` 个是已回放过的历史事件，丢弃
        skipped = 0
        while True:
            item = await ch.queue.get()
            if item is _SENTINEL:
                return
            if skipped < replayed:
                skipped += 1
                continue
            yield item
