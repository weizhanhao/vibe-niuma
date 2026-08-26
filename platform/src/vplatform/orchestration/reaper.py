"""回收器 —— 把设计文档里承诺过但一直没实现的三件事做掉。

1. **僵尸工位**：worker 崩溃后 worktree + 容器 + 磁盘永久泄漏。
   之前 `Workspace` 表零写入，连"收什么"都不知道；现在 stages 会落库，
   这里按记录遍历回收。

2. **jobs 冷热分表**（§7.5 补偿设计 ②）：MySQL 没有部分索引，jobs 表里
   99% 是终态行。`models.py` 注释声称"终态行由 reaper 搬到 jobs_archive"，
   但表和 reaper 都不存在，jobs 会无限增长、`ix_job_claim` 越扫越慢。

3. **过期端口租约**：`PortLeaseManager._reap_expired` 只在 acquire 内部被动
   触发。工位活着但没人 renew 时，端口会被别人抢走。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from vplatform.core.config import get_settings
from vplatform.core.db import session_scope
from vplatform.core.models import (
    Job, JobArchive, PortLease, Requirement, Run, Signal, Step, Workspace,
)

logger = logging.getLogger(__name__)

TERMINAL_JOB_STATES = ("done", "failed")
TERMINAL_REQ_STATES = ("done", "discarded", "failed")


async def reap_workspaces(provider, *, idle_after_s: int = 1800) -> int:
    """回收工位。

    两类：
      - 所属需求已终结的（正常路径，需求跑完就该收）
      - 超过 idle_after_s 还是 ready 的（worker 崩了，没人来收）
    """
    if provider is None:
        return 0
    cutoff = datetime.utcnow() - timedelta(seconds=idle_after_s)
    victims: list[tuple[str, str, dict, str | None]] = []

    with session_scope() as s:
        rows = s.execute(
            select(Workspace).where(Workspace.state == "ready")
        ).scalars().all()
        for ws in rows:
            run = s.get(Run, ws.run_id)
            req_done = False
            if run is not None:
                req = s.execute(
                    select(Requirement).join(
                        Run, Run.task_id.isnot(None)
                    ).where(Requirement.project_id == ws.project_id)
                ).scalars().first()
                req_done = bool(req and req.state in TERMINAL_REQ_STATES)
            if req_done or ws.created_at < cutoff:
                victims.append((ws.id, ws.path, dict(ws.repos or {}),
                                ws.container_id))

    if not victims:
        return 0

    from vplatform.workspace.provider import WorkspaceHandle
    from pathlib import Path

    reclaimed = 0
    for ws_id, path, repos, container in victims:
        handle = WorkspaceHandle(id=ws_id, run_id="", project_id="",
                                 root=Path(path), branch="", repos=repos,
                                 container_id=container)
        try:
            await provider.release(handle, best_effort=True)
        except Exception:  # noqa: BLE001 —— 一个收不掉不能拖死整批
            logger.exception("回收工位 %s 失败", ws_id)
            continue
        with session_scope() as s:
            row = s.get(Workspace, ws_id)
            if row is not None:
                row.state = "released"
                row.released_at = datetime.utcnow()
        reclaimed += 1

    if reclaimed:
        logger.info("回收了 %d 个僵尸工位", reclaimed)
    return reclaimed


def archive_jobs(*, older_than_s: int = 3600, batch: int = 500) -> int:
    """把终态 job 搬到 jobs_archive，让热表只留活跃行。

    连同它的 step 一起搬 —— step 是幂等重放的依据，需求终结后就没用了，
    但要留档便于事后排查。
    """
    cutoff = datetime.utcnow() - timedelta(seconds=older_than_s)
    moved = 0
    with session_scope() as s:
        jobs = s.execute(
            select(Job).where(Job.state.in_(TERMINAL_JOB_STATES),
                              Job.updated_at < cutoff).limit(batch)
        ).scalars().all()
        for job in jobs:
            s.add(JobArchive(
                id=job.id, project_id=job.project_id,
                requirement_id=job.requirement_id, run_id=job.run_id,
                kind=job.kind, lane=job.lane, state=job.state,
                payload=job.payload, idempotency_key=job.idempotency_key,
                attempts=job.attempts, last_error=job.last_error,
                created_at=job.created_at, archived_at=datetime.utcnow()))
            s.execute(delete(Step).where(Step.job_id == job.id))
            # **signals 也必须先删。**
            # signals.job_id 有外键指向 jobs.id —— 不删就是
            # `Cannot delete or update a parent row`，整批归档回滚。
            # 而收过信号的 job 恰恰是最常见的那种（人工闸门、澄清挂起、
            # 重试唤醒都会留信号），于是归档永远失败、reaper 崩溃重试，
            # 热表只增不减 —— MySQL 没有部分索引，热表撑大正是 §7.5
            # 要靠归档避免的那件事。
            s.execute(delete(Signal).where(Signal.job_id == job.id))
            s.delete(job)
            moved += 1
    if moved:
        logger.info("归档了 %d 个终态 job", moved)
    return moved


def reap_port_leases() -> int:
    """删掉过期租约。工位还活着的会由 renew 续上。"""
    with session_scope() as s:
        res = s.execute(
            delete(PortLease).where(PortLease.expires_at < datetime.utcnow()))
        n = res.rowcount or 0
    if n:
        logger.info("回收了 %d 个过期端口租约", n)
    return n


async def run_reaper(*, interval_s: int = 300, provider_factory=None) -> None:
    """常驻回收循环。由 worker_main 起一个。"""
    st = get_settings()
    while True:
        try:
            provider = provider_factory() if provider_factory else None
            await reap_workspaces(provider, idle_after_s=st.port_lease_ttl_s)
            archive_jobs()
            reap_port_leases()
        except Exception:  # noqa: BLE001 —— 回收循环不能被单次异常打死
            logger.exception("reaper 轮次异常")
        await asyncio.sleep(interval_s)
