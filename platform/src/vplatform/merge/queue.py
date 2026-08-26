"""合并队列（§12）—— per-repo 串行。

核心认知：**并行分支各自验证全过 ≠ 合起来能过。**
所以每次 rebase 到最新 target 之后，**必须重跑验证**，通过才 push。
这一步不能省 —— 省了就是把集成回归推给生产。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vplatform.core.models import MergeJob

logger = logging.getLogger(__name__)

# verify(repo_path) -> (ok, detail)
Verifier = Callable[[str], Awaitable[tuple[bool, str]]]


class MergeQueue:
    def __init__(self, session: Session):
        self.s = session

    def enqueue(self, *, project_id: str, requirement_id: str, repo_name: str) -> MergeJob:
        exist = self.s.execute(
            select(MergeJob).where(
                MergeJob.requirement_id == requirement_id,
                MergeJob.repo_name == repo_name,
                MergeJob.state.notin_(("merged", "rejected")),
            )
        ).scalar_one_or_none()
        if exist is not None:
            return exist

        tail = self.s.execute(
            select(func.coalesce(func.max(MergeJob.position), 0)).where(
                MergeJob.project_id == project_id, MergeJob.repo_name == repo_name)
        ).scalar_one()
        job = MergeJob(project_id=project_id, requirement_id=requirement_id,
                       repo_name=repo_name, position=int(tail) + 1)
        self.s.add(job)
        self.s.flush()
        return job

    def head(self, *, project_id: str, repo_name: str) -> MergeJob | None:
        """队首。**per-repo 串行**：同一个仓同时只处理一条。"""
        active = self.s.execute(
            select(MergeJob).where(
                MergeJob.project_id == project_id, MergeJob.repo_name == repo_name,
                MergeJob.state.in_(("rebasing", "conflict", "resolving", "verifying")),
            ).order_by(MergeJob.position).limit(1)
        ).scalar_one_or_none()
        if active is not None:
            return active
        return self.s.execute(
            select(MergeJob).where(
                MergeJob.project_id == project_id, MergeJob.repo_name == repo_name,
                MergeJob.state == "queued",
            ).order_by(MergeJob.position).limit(1)
        ).scalar_one_or_none()

    def pending(self, *, project_id: str, repo_name: str) -> list[MergeJob]:
        return list(self.s.execute(
            select(MergeJob).where(
                MergeJob.project_id == project_id, MergeJob.repo_name == repo_name,
                MergeJob.state.notin_(("merged", "rejected")),
            ).order_by(MergeJob.position)
        ).scalars())

    def reorder_by_touch_risk(self, *, project_id: str, repo_name: str,
                              risky_requirement_ids: set[str]) -> list[MergeJob]:
        """把 touches 相交的需求排成前后，不让它们并排撞车（§8.3 保险 ①）。

        这就是拆解阶段声明 touches 的兑现点：冲突预防前置到调度期，
        而不是全堆到合并期让 AI 收拾。
        """
        jobs = self.pending(project_id=project_id, repo_name=repo_name)
        jobs.sort(key=lambda j: (j.requirement_id in risky_requirement_ids, j.position))
        for i, j in enumerate(jobs, start=1):
            j.position = i
        self.s.flush()
        return jobs

    def mark(self, job: MergeJob, state: str, *, ladder: list[dict] | None = None,
             sha: str | None = None) -> None:
        job.state = state
        if ladder is not None:
            job.conflict_ladder = ladder
        if sha:
            job.merged_sha = sha
        self.s.flush()
