"""Job 存储层 —— 抢占 / 幂等 / 人工 gate（§7）。

MySQL 8 上的三处关键实现（§7.5）：
- 抢占是**事务里三步**（SELECT FOR UPDATE SKIP LOCKED → UPDATE → 读回），
  因为 MySQL 没有 `UPDATE ... RETURNING`
- 唤醒靠**把 next_run_at 置为 now** + 分级轮询，因为没有 LISTEN/NOTIFY
- 终态行由 reaper 搬走，因为没有部分索引
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vplatform.core.models import Job, Signal, Step

INTERACTIVE = "interactive"
BACKGROUND = "background"


class JobStore:
    def __init__(self, session: Session):
        self.s = session

    # ── 入队 ────────────────────────────────────────────────────
    def enqueue(
        self,
        *,
        project_id: str,
        kind: str,
        payload: dict | None = None,
        idempotency_key: str,
        lane: str = BACKGROUND,
        requirement_id: str | None = None,
        run_id: str | None = None,
        delay_s: float = 0,
        max_attempts: int = 3,
    ) -> Job:
        """入队。**幂等键重复时返回已有 job，不报错也不重复入队。**

        这是重试安全的基础：同一个逻辑动作无论被触发多少次，只产生一个 job。
        """
        existing = self.s.execute(
            select(Job).where(Job.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        job = Job(
            project_id=project_id,
            requirement_id=requirement_id,
            run_id=run_id,
            kind=kind,
            lane=lane,
            payload=payload or {},
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            next_run_at=datetime.utcnow() + timedelta(seconds=delay_s),
        )
        # **用 SAVEPOINT，不能用 session.rollback()。**
        #
        # `Session.rollback()` 回滚的是**调用方的整个事务** —— 它会 expunge
        # 事务内所有 pending→persistent 的对象、还原所有已修改对象。
        # 这个方法跑在 worker 的事务里，此前已经 flush 了 req.stage、Step 行、
        # Event 行、claim 的状态。幂等键冲突时全部丢失，随后 job 被标 done、
        # 幂等键被占死 —— 那个环节永远不会再跑。
        try:
            with self.s.begin_nested():
                self.s.add(job)
                self.s.flush()
        except IntegrityError:
            return self.s.execute(
                select(Job).where(Job.idempotency_key == idempotency_key)
            ).scalar_one()
        return job

    # ── 抢占 ────────────────────────────────────────────────────
    def claim(self, *, worker_id: str, lane: str | None = None,
              lock_timeout_s: int = 900) -> Job | None:
        """抢一个待办 job。返回 None 表示没有。

        MySQL 没有 `UPDATE ... RETURNING`，所以是三步：
            SELECT id ... FOR UPDATE SKIP LOCKED  →  UPDATE  →  按 id 读回

        `SKIP LOCKED` MySQL 8.0.1 起支持 —— 这一点原设计说错过，实测可用。
        同时回收 locked_at 超时的僵尸 job（worker 崩溃后由别人接管）。
        """
        now = datetime.utcnow()
        stale = now - timedelta(seconds=lock_timeout_s)

        stmt = select(Job.id).where(
            ((Job.state == "pending") & (Job.next_run_at <= now))
            | ((Job.state == "running") & (Job.locked_at < stale))
        )
        if lane:
            stmt = stmt.where(Job.lane == lane)
        stmt = stmt.order_by(Job.next_run_at).limit(1)

        # sqlite 不支持 SKIP LOCKED；MySQL/Postgres 支持
        if self.s.bind is not None and self.s.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update(skip_locked=True)

        job_id = self.s.execute(stmt).scalar_one_or_none()
        if job_id is None:
            return None

        res = self.s.execute(
            update(Job)
            .where(Job.id == job_id, Job.state.in_(("pending", "running")))
            .values(state="running", locked_by=worker_id, locked_at=now,
                    attempts=Job.attempts + 1)
        )
        if res.rowcount == 0:      # 被别人抢走了
            return None
        self.s.flush()
        return self.s.get(Job, job_id)

    # ── 结束 ────────────────────────────────────────────────────
    def finish(self, job: Job, *, state: str = "done", error: str | None = None) -> None:
        job.state = state
        job.last_error = error
        job.locked_by = None
        job.locked_at = None
        self.s.flush()

    def retry_later(self, job: Job, *, delay_s: float, error: str) -> bool:
        """安排重试。返回 False 表示已达上限、已置 failed。"""
        if job.attempts >= job.max_attempts:
            self.finish(job, state="failed", error=error)
            return False
        job.state = "pending"
        job.locked_by = None
        job.locked_at = None
        job.last_error = error
        job.next_run_at = datetime.utcnow() + timedelta(seconds=delay_s)
        self.s.flush()
        return True

    # ── 人工 gate（§7.3 ③）─────────────────────────────────────
    def park(self, job: Job) -> None:
        """挂起等信号。**不占 worker** —— 审核期间工位已经回收，别人能用。"""
        job.state = "awaiting_signal"
        job.locked_by = None
        job.locked_at = None
        # 远期，避免被轮询扫到；signal 到达时会拉回 now
        job.next_run_at = datetime.utcnow() + timedelta(days=3650)
        self.s.flush()

    def signal(self, job_id: str, name: str, payload: dict | None = None) -> Signal:
        """投递信号并**立刻把 job 拉回可调度**。

        MySQL 没有 LISTEN/NOTIFY，所以唤醒 = 把 next_run_at 置为 now，
        让下一轮轮询捡到。交互 lane 200ms，人点完按钮基本无感。
        """
        job = self.s.get(Job, job_id)
        if job is None:
            raise LookupError(f"job {job_id} 不存在")
        sig = Signal(project_id=job.project_id, job_id=job_id, name=name,
                     payload=payload or {})
        self.s.add(sig)
        if job.state == "awaiting_signal":
            job.state = "pending"
            job.lane = INTERACTIVE      # 人在等，升到交互 lane
            job.next_run_at = datetime.utcnow()
        self.s.flush()
        return sig

    def take_signal(self, job_id: str, name: str) -> Signal | None:
        sig = self.s.execute(
            select(Signal)
            .where(Signal.job_id == job_id, Signal.name == name, Signal.consumed.is_(False))
            .order_by(Signal.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if sig is not None:
            sig.consumed = True
            self.s.flush()
        return sig

    # ── 幂等 step（Temporal replay 的最小自建版）────────────────
    def step_result(self, job: Job, name: str) -> dict | None:
        """已完成的 step 直接返回 output；未做过返回 None。"""
        st = self.s.execute(
            select(Step).where(Step.job_id == job.id, Step.name == name)
        ).scalar_one_or_none()
        if st is not None and st.state == "done":
            return st.output or {}
        return None

    def record_step(self, job: Job, name: str, *, output: dict,
                    input: dict | None = None, seq: int = 0) -> None:
        st = self.s.execute(
            select(Step).where(Step.job_id == job.id, Step.name == name)
        ).scalar_one_or_none()
        if st is None:
            st = Step(project_id=job.project_id, job_id=job.id, name=name, seq=seq,
                      input=input or {})
            self.s.add(st)
        st.output = output
        st.state = "done"
        self.s.flush()


def new_worker_id() -> str:
    return f"w-{uuid.uuid4().hex[:8]}"
