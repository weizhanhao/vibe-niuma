"""事件总线（§13）—— 职责拆开：Redis 管实时，MySQL 管真相。

v1 用进程内 dict + asyncio.Queue，单进程绑死，orchestrator 无法多副本。
这里保留它 buffer + replay 的**语义**（那个设计是对的），只换传输。

    events 表（MySQL，自增 id）  持久化 + 回放。断线重连、事后审计都读它
    Redis Streams                实时 fan-out。多副本 orchestrator + 多个 SSE 连接

为什么不是 LISTEN/NOTIFY：MySQL 没有（D4）。
为什么 Redis 不能拖到后期：agent 日志每秒几十行，纯 DB 轮询扛不住（§13）。

Redis 缺席时自动退化为进程内 fan-out —— 单机开发能跑，但**多副本部署必须配 Redis**。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import select

from vplatform.core.config import get_settings
from vplatform.core.db import session_scope
from vplatform.core.models import Event

logger = logging.getLogger(__name__)

# 单条流最多缓冲多少条思考。够一次 agent 运行看全，又不会把内存吃掉。
_LIVE_BUFFER = 400

# XREAD 阻塞多久。socket 超时要比它大，否则安静窗口会被当成连接超时。
_XREAD_BLOCK_MS = 15_000


class _RedisTimeout(Exception):
    """占位。真正的 redis.TimeoutError 在 _redis_timeout() 里解析。"""


def _redis_timeout() -> type[BaseException]:
    try:
        import redis.exceptions as rexc
        return rexc.TimeoutError
    except ImportError:
        return _RedisTimeout


@dataclass(frozen=True)
class Emitted:
    id: int
    stream: str
    kind: str
    payload: dict
    # 只走实时通道、不落库的事件（agent 的思考过程）。见 publish_live()。
    ephemeral: bool = False

    def sse(self) -> str:
        body = json.dumps({"kind": self.kind, **self.payload}, ensure_ascii=False)
        if self.ephemeral:
            # **不能带 id。** 浏览器 EventSource 会把收到的最后一个 id 记成
            # lastEventId，断线重连时带回来。临时事件的 id 是 0，
            # 带上就会把游标重置成 0 —— 重连后整条历史重放一遍。
            return f"event: {self.kind}\ndata: {body}\n\n"
        return f"id: {self.id}\nevent: {self.kind}\ndata: {body}\n\n"


class EventBus:
    def __init__(self, *, redis_url: str | None = None):
        self._redis_url = redis_url if redis_url is not None else get_settings().redis_url
        self._redis = None
        # 进程内订阅者：{stream: [queue]}。Redis 在时仍用它服务本进程的订阅，
        # 省一次网络往返。
        self._local: dict[str, list[asyncio.Queue]] = {}
        # 思考流的环形缓冲：{stream: [Emitted]}。只在内存里，进程重启即丢 ——
        # 它是过程不是真相，丢了不影响任何业务判断。
        self._live: dict[str, list[Emitted]] = {}
        # 持有 fire-and-forget 的推流 task —— 不持有的话可能被 GC 掉
        self._tasks: set = set()
        # 本实例的标记。Redis 会把自己发的事件再送回来 —— 持久事件靠 id
        # 去重，临时事件 id 都是 0 去不了重，会在页面上出现两遍。
        self._origin = uuid.uuid4().hex[:12]

    async def _get_redis(self):
        if not self._redis_url:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
            except ImportError:
                logger.warning("配了 redis_url 但没装 redis 包 —— 退化为单进程 fan-out")
                self._redis_url = ""
                return None
            # **socket 超时必须大于 XREAD 的 block 时长。**
            # 默认 socket_timeout 比 block 短的话，每次「这段时间没有新事件」
            # 都会抛 TimeoutError —— 看起来像 Redis 挂了，实际只是没消息。
            self._redis = aioredis.from_url(
                self._redis_url, decode_responses=True,
                socket_timeout=_XREAD_BLOCK_MS / 1000 + 10,
                socket_connect_timeout=5, health_check_interval=30)
        return self._redis

    # ── 发布（outbox 两段式）────────────────────────────────────
    def record(self, session, *, project_id: str, stream: str, kind: str,
               payload: dict | None = None) -> Emitted:
        """**在调用方的事务里**写事件行。不 fan-out。

        为什么必须同事务：事件与状态变更是一次业务动作的两半。分两个事务写
        （dual-write）会有两个后果 —— 中间崩溃留下不一致；在 sqlite 上直接死锁，
        因为 worker 的事务还开着。

        fan-out 留到事务提交之后由 dispatch() 做，这就是 outbox 模式。
        """
        payload = payload or {}
        ev = Event(project_id=project_id, stream=stream, kind=kind, payload=payload)
        session.add(ev)
        session.flush()
        return Emitted(id=ev.id, stream=stream, kind=kind, payload=payload)

    async def dispatch(self, *emitted: Emitted) -> None:
        """事务提交后再推。推失败不回滚业务 —— 事件已经落库，回放拿得到。"""
        for e in emitted:
            for q in list(self._local.get(e.stream, ())):
                q.put_nowait(e)
        r = await self._get_redis()
        if r is None:
            return
        for e in emitted:
            try:
                await r.xadd(f"vp:{e.stream}", {"d": json.dumps(
                    {"id": e.id, "kind": e.kind, "payload": e.payload,
                     "src": self._origin}, ensure_ascii=False)},
                    maxlen=get_settings().event_buffer, approximate=True)
            except Exception:  # noqa: BLE001 —— 实时通道挂了不能影响业务
                logger.exception("Redis xadd 失败，事件已落库，仅实时推送丢失")

    def publish_live(self, *, stream: str, kind: str,
                     payload: dict | None = None) -> None:  # noqa: D401
        """只 fan-out，**不落库、不开事务**。

        agent 的思考过程一次运行几十上百条，而且它跑的时候 worker 的事务
        正开着 —— 每条都 publish() 会开第二个 session，正是本模块开头
        警告的 dual-write：中途崩溃留不一致，sqlite 上直接死锁。

        所以思考流只走实时通道。**它是过程不是真相**：最终结论会作为
        Message 落库（连同一份精简 trace），断线重放拿的是那个。
        为了让中途打开页面的人也能看到已经发生的部分，这里留一个环形缓冲。
        """
        ev = Emitted(id=0, stream=stream, kind=kind, payload=payload or {},
                     ephemeral=True)
        buf = self._live.setdefault(stream, [])
        buf.append(ev)
        if len(buf) > _LIVE_BUFFER:
            del buf[:-_LIVE_BUFFER]
        for q in list(self._local.get(stream, ())):
            q.put_nowait(ev)
        # **worker 和 API 是两个进程。**
        # 只发本进程队列的话，思考流永远到不了浏览器 —— worker 在这边跑，
        # SSE 客户端挂在那边。跨进程只能靠 Redis。
        self._fanout_redis(ev)

    def _fanout_redis(self, ev: Emitted) -> None:
        """把事件甩进 Redis Stream。**不阻塞调用方** —— 它在 agent 的
        回调里，推流慢一点也不该拖住这次开发。"""
        if not self._redis_url:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return                      # 没有事件循环（同步测试）就只走本地
        task = loop.create_task(self._xadd(ev))
        # 存一份引用，否则 task 可能在跑完前被 GC 掉
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _xadd(self, ev: Emitted) -> None:
        r = await self._get_redis()
        if r is None:
            return
        try:
            await r.xadd(f"vp:{ev.stream}", {"d": json.dumps(
                {"id": ev.id, "kind": ev.kind, "payload": ev.payload,
                 "ephemeral": ev.ephemeral, "src": self._origin},
                ensure_ascii=False)},
                maxlen=get_settings().event_buffer, approximate=True)
        except Exception:  # noqa: BLE001 —— 实时通道挂了不能影响业务
            logger.exception("Redis xadd 失败（思考流），业务不受影响")

    def live_backlog(self, stream: str) -> list[Emitted]:
        """这条流上还没结束的那次运行，已经吐出来的思考。

        `run_start` 是内部标记，不算思考 —— 漏掉这个过滤的话
        页面上会多出一条空白「步骤」。
        """
        return [e for e in self._live.get(stream, ()) if e.kind != "run_start"]

    def clear_live(self, stream: str) -> None:
        """一次运行开始/结束时清缓冲 —— 下次运行不该看到上次的残留。

        同时往 Redis 打一个 `run_start` 标记：跨进程的订阅者靠它知道
        「这次运行是从哪一条开始的」，才能把已经发生的思考补齐。
        """
        self._live.pop(stream, None)
        self.publish_live(stream=stream, kind="run_start", payload={})

    async def publish(self, *, project_id: str, stream: str, kind: str,
                      payload: dict | None = None) -> Emitted:
        """自带事务的便捷版 —— 给**不在事务里**的调用方（API 路由等）用。

        在 worker handler 里**不要用它**，用 record() + dispatch()。
        """
        with session_scope() as s:
            emitted = self.record(s, project_id=project_id, stream=stream,
                                  kind=kind, payload=payload)
        await self.dispatch(emitted)
        return emitted

    # ── 订阅（带断线回放）───────────────────────────────────────
    async def subscribe(self, *, project_id: str, stream: str,
                        last_event_id: int = 0) -> AsyncIterator[Emitted]:
        """先从 MySQL 补齐 > last_event_id 的历史，再挂到实时通道。

        这就是断线重连不丢事件的机制：客户端把上次收到的 id 带回来。
        """
        q: asyncio.Queue = asyncio.Queue()
        self._local.setdefault(stream, []).append(q)
        # **必须在 try 之前定义。** 客户端在回放历史那几行里就断开的话，
        # finally 会在 pump 赋值之前跑到 —— UnboundLocalError 把真正的
        # 断开原因盖掉。
        pump: asyncio.Task | None = None
        try:
            # **用已见集合而不是阈值游标。**
            #
            # 自增 id 的分配顺序 ≠ 提交顺序：worker A 的长事务先拿到 id=100 但
            # 后提交，worker B 拿 id=101 先提交。此刻订阅者回放只看得到 101，
            # 若用 `replayed = 101` 当阈值，A 的 100 到达时会被
            # `ev.id <= replayed` 丢弃；客户端下次带 lastEventId=101 重连，
            # 查 `id > 101` 也补不回 100 —— **永久丢事件**。
            #
            # 集合去重不受提交顺序影响；只记本次会话发过的 id，内存有界
            # （单个 SSE 连接的生命周期内）。
            seen: set[int] = set()
            with session_scope() as s:
                rows = s.execute(
                    select(Event)
                    .where(Event.project_id == project_id, Event.stream == stream,
                           Event.id > last_event_id)
                    .order_by(Event.id)
                ).scalars().all()
                history = [Emitted(id=e.id, stream=stream, kind=e.kind,
                                   payload=e.payload or {}) for e in rows]
            for ev in history:
                seen.add(ev.id)
                yield ev

            # 正在跑的那次运行已经吐出来的思考 —— 中途打开页面的人也要看得到
            for ev in self.live_backlog(stream):
                yield ev

            # **跨进程时，本地缓冲是空的。**
            # 思考缓冲在 worker 进程的内存里，而 SSE 挂在 API 进程 ——
            # 晚连上来的客户端从 `$` 开始读，前面已经吐出来的思考全丢，
            # 页面上就是一句「正在想… 0 步」。所以要先从 Redis 把
            # 本次运行（上一个 run_start 之后）的补齐。
            r0 = await self._get_redis()
            replay_from = "$"
            if r0 is not None:
                backlog, replay_from = await self._live_backlog_redis(r0, stream)
                for ev in backlog:
                    yield ev

            # **必须消费 Redis。**
            # 之前 dispatch() 往 Redis xadd，但没有任何地方 XREAD ——
            # 写进去就没人读，跨进程实时等于不存在：API 进程的订阅者只能
            # 看到连接那一刻从 MySQL 补的历史，worker 之后发的一条都收不到。
            if r0 is not None:
                pump = asyncio.create_task(
                    self._pump_redis(r0, stream, q, start=replay_from))

            while True:
                ev = await q.get()
                if ev.ephemeral:
                    # 临时事件没有 id，去重和阈值都不适用（id=0 会被
                    # `<= last_event_id` 全部吃掉）
                    yield ev
                    continue
                # 只跳过「本次已发过」和「客户端明确说收到过」的
                if ev.id in seen or ev.id <= last_event_id:
                    continue
                seen.add(ev.id)
                yield ev
        finally:
            if pump is not None:
                pump.cancel()
            subs = self._local.get(stream, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._local.pop(stream, None)

    async def _live_backlog_redis(self, r, stream: str):
        """把「本次运行已经发生的思考」从 Redis 里捞出来。

        往回读最近若干条，找到最后一个 `run_start`，只回放它之后的 ——
        上一次运行的残留不该混进来。返回 (事件列表, 之后从哪个 id 继续 tail)。
        """
        try:
            rows = await r.xrevrange(f"vp:{stream}", "+", "-", count=_LIVE_BUFFER)
        except Exception:  # noqa: BLE001
            return [], "$"
        if not rows:
            return [], "$"
        newest_id = rows[0][0]
        out: list[Emitted] = []
        for entry_id, fields in rows:            # 从新到旧
            try:
                d = json.loads(fields["d"])
            except Exception:  # noqa: BLE001
                continue
            if not d.get("ephemeral"):
                continue                          # 持久事件由 MySQL 回放负责
            if d.get("kind") == "run_start":
                break                             # 到本次运行的起点了，停
            out.append(Emitted(id=0, stream=stream, kind=str(d.get("kind") or "log"),
                               payload=d.get("payload") or {}, ephemeral=True))
        out.reverse()
        return out, newest_id

    async def _pump_redis(self, r, stream: str, q: asyncio.Queue,
                          *, start: str = "$") -> None:
        _TIMEOUT_EXC = _redis_timeout()
        """把 Redis Stream 上的新事件搬进本地队列。

        从 `$` 起读 —— 历史由 MySQL 回放负责，这里只管「从现在起」。
        """
        last = start
        try:
            while True:
                try:
                    res = await r.xread({f"vp:{stream}": last}, count=64,
                                        block=_XREAD_BLOCK_MS)
                except asyncio.CancelledError:
                    raise
                except _TIMEOUT_EXC:
                    # **这不是错误，是「这段时间没有新事件」。**
                    # 当成错误退出的话，第一个安静的窗口就把推流永久掐断：
                    # agent 后面吐的思考一条都到不了浏览器。
                    continue
                except Exception:  # noqa: BLE001
                    logger.exception("Redis xread 失败，实时通道退化为仅本进程")
                    return
                for _key, entries in res or []:
                    for entry_id, fields in entries:
                        last = entry_id
                        try:
                            d = json.loads(fields["d"])
                        except Exception:  # noqa: BLE001
                            continue
                        if d.get("src") == self._origin:
                            continue      # 自己发的，本地队列已经收过
                        if d.get("kind") == "run_start":
                            continue      # 内部标记，不给客户端
                        ev = Emitted(id=int(d.get("id") or 0), stream=stream,
                                     kind=str(d.get("kind") or "log"),
                                     payload=d.get("payload") or {},
                                     ephemeral=bool(d.get("ephemeral")))
                        # 本进程自己发的已经进过队列了，别重复
                        q.put_nowait(ev)
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> None:
    global _bus
    _bus = None
