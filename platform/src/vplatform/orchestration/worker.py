"""Worker 池 —— 分级轮询 + 指数退避（§7.5 ①）。

为什么是轮询：MySQL 没有 LISTEN/NOTIFY（D4）。

为什么分级：**别为了低延迟把间隔压到 50ms，那是白烧 DB。**
    interactive  200ms  —— 人刚点了「通过」「重试」「回答澄清」，要秒回
    background   2s 起，空转指数退避到 5s —— agent 跑代码/构建/部署本来就是分钟级

人工 gate 的唤醒延迟上限 = interactive 间隔 = 200ms，人完全无感。
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable

from vplatform.core.config import get_settings
from vplatform.core.db import session_scope
from vplatform.core.models import Job, Requirement
from vplatform.orchestration.jobs import BACKGROUND, INTERACTIVE, JobStore, new_worker_id

logger = logging.getLogger(__name__)

# handler(job_payload, ctx) -> dict|None；抛异常即失败，由 worker 决定重试
Handler = Callable[["JobContext"], Awaitable[dict | None]]


class JobContext:
    """交给 handler 的执行上下文。

    `step()` 是幂等边界：同名 step 做过就直接返回缓存 output，不重跑。
    进程被 kill 后重来，已完成的 step 会被跳过 —— 这就是 Temporal replay 的最小自建版。
    """

    def __init__(self, job: Job, store: JobStore):
        self.job = job
        self.store = store
        self.payload = job.payload or {}
        self._seq = 0
        # outbox：事件与状态同事务落库，提交后才 fan-out（见 core/events.py）
        self._outbox: list = []

    @property
    def session(self):
        """**handler 必须用这个 session，不要自己开 session_scope()。**

        业务状态与 job 状态是一次动作的两半，必须同事务：分开写会在崩溃时
        留下「job 说做完了但业务没变」的不一致；在 sqlite 上还会直接死锁。
        """
        return self.store.s

    def emit(self, kind: str, **payload) -> None:
        """在**当前事务里**记一条事件。提交后由 worker 统一 dispatch。"""
        from vplatform.core.events import get_bus
        self._outbox.append(get_bus().record(
            self.store.s, project_id=self.job.project_id,
            stream=f"req:{self.job.requirement_id}", kind=kind, payload=payload))

    async def step(self, name: str, fn: Callable[[], Awaitable[dict]]) -> dict:
        cached = self.store.step_result(self.job, name)
        if cached is not None:
            return cached
        self._seq += 1
        out = await fn()
        self.store.record_step(self.job, name, output=out or {}, seq=self._seq)
        return out or {}

    def park_for_signal(self) -> None:
        """挂起等人。不占 worker。"""
        self.store.park(self.job)

    def take_signal(self, name: str):
        return self.store.take_signal(self.job.id, name)


class ParkedForSignal(Exception):
    """handler 抛它表示「我要等人，别当失败」。"""


class Registry:
    def __init__(self) -> None:
        self._h: dict[str, Handler] = {}

    def register(self, kind: str):
        def deco(fn: Handler) -> Handler:
            self._h[kind] = fn
            return fn
        return deco

    def get(self, kind: str) -> Handler | None:
        return self._h.get(kind)


registry = Registry()


class Worker:
    def __init__(self, *, lane: str = BACKGROUND, worker_id: str | None = None,
                 reg: Registry | None = None, settings=None):
        self.lane = lane
        self.id = worker_id or new_worker_id()
        self.reg = reg or registry
        self.st = settings or get_settings()
        self._stop = asyncio.Event()
        self._idle_rounds = 0

    def stop(self) -> None:
        self._stop.set()

    # ── 轮询间隔（分级 + 退避）──────────────────────────────────
    def _sleep_seconds(self, *, did_work: bool) -> float:
        if self.lane == INTERACTIVE:
            return self.st.poll_interactive_ms / 1000
        if did_work:
            self._idle_rounds = 0
            return 0.0                       # 有活就接着干，不睡
        self._idle_rounds += 1
        base = self.st.poll_background_ms
        # 指数退避到上限：2s → 4s → 5s(封顶)
        ms = min(base * (2 ** (self._idle_rounds - 1)), self.st.poll_backoff_max_ms)
        return ms / 1000

    async def run_once(self) -> bool:
        """跑一轮。返回是否处理了 job。"""
        # 抢占单独一个短事务提交 —— 这样 attempts 自增和 state=running 立刻落库。
        # 之前它跟 handler 在同一个大事务里：进程被 OOM-kill 时整个事务回滚，
        # attempts 没加、state 回 pending，于是毒丸 job 被无限重抢，
        # 永远到不了 max_attempts，一个坏 job 能把整条 lane 锁死在崩溃循环里。
        with session_scope() as s0:
            claimed = JobStore(s0).claim(
                worker_id=self.id, lane=self.lane,
                lock_timeout_s=self.st.job_lock_timeout_s)
            job_id = claimed.id if claimed is not None else None
        if job_id is None:
            return False

        with session_scope() as s:
            store = JobStore(s)
            job = s.get(Job, job_id)
            if job is None:
                return False
            failure: str | None = None
            pending: list = []
            handler = self.reg.get(job.kind)
            if handler is None:
                store.finish(job, state="failed", error=f"没有注册 kind={job.kind!r} 的 handler")
                return True
            ctx = JobContext(job, store)
            try:
                await handler(ctx)
            except ParkedForSignal:
                store.park(job)
                pending = list(ctx._outbox)
            except Exception as exc:  # noqa: BLE001
                failure = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}"
            else:
                if job.state == "running":       # handler 没自己 park
                    store.finish(job, state="done")
                pending = list(ctx._outbox)

        if failure is not None:
            # **handler 失败必须整体回滚业务写**，不能半提交。
            # 之前异常被吃在 with 内部，session_scope 照常 commit ——
            # 于是 handler 改到一半的 Requirement 被提交、已 record 的事件被
            # dispatch，然后 job 再重试一遍，副作用翻倍（重建 Run、重开工位、
            # 重烧 token）。更糟的是若异常发生在 `req.stage = nxt.key` 之后，
            # 重试时会去跑**别的环节**的主体。
            with session_scope() as s2:
                st2 = JobStore(s2)
                j2 = s2.get(Job, job_id)
                if j2 is not None:
                    delay = 2 * (4 ** max(0, j2.attempts - 1))
                    if st2.retry_later(j2, delay_s=delay, error=failure):
                        logger.warning("job %s 第 %d 次失败，%ss 后重试",
                                       job_id, j2.attempts, delay)
                    else:
                        logger.error("job %s 达重试上限，置 failed", job_id)
                        # **需求也要跟着标失败。**
                        # 只把 job 置 failed 的话，需求还是 active：
                        # 看板上显示「在跑」，重试接口回「正在跑，不用重试」，
                        # 于是它永远卡在那儿，谁也推不动 —— 唯一的出路是
                        # 去数据库改状态。业务写在 handler 里已经被回滚了，
                        # 这里必须单独补一笔。
                        if j2.requirement_id:
                            req2 = s2.get(Requirement, j2.requirement_id)
                            if req2 is not None and req2.state == "active":
                                req2.state = "failed"
            return True

        # **事务已提交**，现在才 fan-out。提交前推会让订阅者读到还没落库的状态。
        if pending:
            from vplatform.core.events import get_bus
            await get_bus().dispatch(*pending)
        return True

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                did = await self.run_once()
            except Exception:  # noqa: BLE001 —— worker 循环不能被单次异常打死
                logger.exception("worker %s 轮询异常", self.id)
                did = False
            delay = self._sleep_seconds(did_work=did)
            if delay <= 0:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


async def run_pool(*, interactive: int = 1, background: int = 2,
                   reg: Registry | None = None) -> None:
    """起一个 worker 池：交互 lane + 后台 lane 各若干。"""
    workers = [Worker(lane=INTERACTIVE, reg=reg) for _ in range(interactive)]
    workers += [Worker(lane=BACKGROUND, reg=reg) for _ in range(background)]
    await asyncio.gather(*(w.run_forever() for w in workers))
