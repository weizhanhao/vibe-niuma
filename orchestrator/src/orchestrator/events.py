"""EventBus —— 每个变更请求一个事件队列，供 SSE 端点订阅、Pipeline 发布。

历史事件保留在 buffer 里：晚订阅的客户端（或 SSE 重连）也能拿到此前的状态变迁。
publish 把事件同时写 buffer（供回放）和 queue（供实时）；subscribe 先回放 buffer，
再消费 queue —— 跳过 queue 里与已回放历史等量的旧事件以避免重复。

Phase F：新增 `log` 事件类型 + `publish_log` helper。log 事件可能成百上千条，
buffer 用 deque(maxlen=BUFFER_MAX_EVENTS) 防爆；溢出按 FIFO 丢老的。
"""
import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

# buffer 上限：log 事件密集时（如 dev runner 几百行输出）防止无界增长
BUFFER_MAX_EVENTS = 1000


@dataclass
class Event:
    type: str  # "status" | "question" | "variants" | "log"
    data: dict


_SENTINEL = object()


@dataclass
class _Channel:
    # deque(maxlen) 自动 FIFO 丢老的；queue 不限上限（消费者负责跟进）
    buffer: deque = field(default_factory=lambda: deque(maxlen=BUFFER_MAX_EVENTS))
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    closed: bool = False
    # 累计 publish 次数：subscribe 跳过 queue 里前 N 个已回放历史用
    published_total: int = 0


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
        ch.published_total += 1
        await ch.queue.put(event)

    async def publish_log(self, request_id: str, phase: str, line: str) -> None:
        """便利方法：发一条 phase=clarifying/locating/coding/building/init 的 log。

        log 行截 200 字符；非空白才发（剔除心跳/换行空消息）。
        """
        text = (line or "").strip()
        if not text:
            return
        await self.publish(
            request_id,
            Event(
                type="log",
                data={
                    "phase": phase,
                    "line": text[:200],
                    "ts": datetime.utcnow().isoformat(),
                },
            ),
        )

    def history(self, request_id: str) -> list[Event]:
        """已发布事件的历史快照 —— 即 subscribe 回放给晚到客户端的那批。"""
        return list(self._channel(request_id).buffer)

    def close(self, request_id: str) -> None:
        ch = self._channel(request_id)
        ch.closed = True
        ch.queue.put_nowait(_SENTINEL)

    async def subscribe(self, request_id: str):
        """异步生成器：先回放 buffer 里的历史事件，再实时产出后续新事件。

        注意：buffer 可能已被 deque(maxlen) 截过头，published_total 是全量计数，
        queue 里仍含所有事件 —— 跳过 published_total 个旧条目即跟实时对齐。
        """
        ch = self._channel(request_id)
        replayed_total = ch.published_total
        for evt in list(ch.buffer):
            yield evt
        # queue 里前 `replayed_total` 个是已 publish 过的历史事件，丢弃
        skipped = 0
        while True:
            item = await ch.queue.get()
            if item is _SENTINEL:
                return
            if skipped < replayed_total:
                skipped += 1
                continue
            yield item
