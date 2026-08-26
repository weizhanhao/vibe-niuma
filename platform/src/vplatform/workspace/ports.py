"""端口租约（§5.3 坑 2）。

v1 硬编码全局 5100-5199，没有分配记录 —— 两个 worker 会分到同一个端口。

这里靠 **DB 唯一索引 (project_id, port)** 让抢占在数据库层就失败，
不靠应用层"先查再插"那种有竞态的协调。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vplatform.core.models import PortLease, Project


class NoPortAvailable(RuntimeError):
    """配额内没有空闲端口。调用方应排队而不是硬等。"""


class PortLeaseManager:
    def __init__(self, session: Session, *, ttl_s: int = 1800):
        self.s = session
        self.ttl = ttl_s

    def _reap_expired(self, project_id: str) -> int:
        """回收过期租约。TTL 是兜底 —— 正常路径是 Run 终结时主动释放。"""
        res = self.s.execute(
            delete(PortLease).where(
                PortLease.project_id == project_id,
                PortLease.expires_at < datetime.utcnow(),
            )
        )
        return res.rowcount or 0

    def acquire(self, *, project_id: str, workspace_id: str) -> int:
        """租一个端口。并发安全：唯一索引冲突就换下一个，不是报错。"""
        project = self.s.get(Project, project_id)
        if project is None:
            raise LookupError(f"project {project_id} 不存在")

        self._reap_expired(project_id)
        taken = set(
            self.s.execute(
                select(PortLease.port).where(PortLease.project_id == project_id)
            ).scalars().all()
        )
        expires = datetime.utcnow() + timedelta(seconds=self.ttl)

        for port in range(project.port_min, project.port_max + 1):
            if port in taken:
                continue
            # SAVEPOINT 而不是 session.rollback()：后者会回滚调用方的整个事务，
            # 把此前 flush 的 Run / Workspace 记录一起抹掉（实测确认）。
            try:
                with self.s.begin_nested():
                    self.s.add(PortLease(project_id=project_id, port=port,
                                         workspace_id=workspace_id,
                                         expires_at=expires))
                    self.s.flush()
            except IntegrityError:
                # 另一个 worker 在我们查完之后抢走了这个端口 —— 换下一个
                taken.add(port)
                continue
            return port

        raise NoPortAvailable(
            f"空间 {project.slug} 的端口段 {project.port_min}-{project.port_max} 已满"
        )

    def release(self, *, project_id: str, workspace_id: str) -> int:
        res = self.s.execute(
            delete(PortLease).where(
                PortLease.project_id == project_id,
                PortLease.workspace_id == workspace_id,
            )
        )
        self.s.flush()
        return res.rowcount or 0

    def renew(self, *, project_id: str, workspace_id: str) -> None:
        """续租。长时间运行的 workspace 要定期调，否则被 TTL 回收。"""
        for lease in self.s.execute(
            select(PortLease).where(
                PortLease.project_id == project_id,
                PortLease.workspace_id == workspace_id,
            )
        ).scalars():
            lease.expires_at = datetime.utcnow() + timedelta(seconds=self.ttl)
        self.s.flush()
