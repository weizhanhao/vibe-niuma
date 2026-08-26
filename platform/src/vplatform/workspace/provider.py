"""Workspace 隔离层的接口（§5.2）。

一个 Workspace = 一个 Run 的隔离工位：
    N 个 git worktree（每个仓一个）+ 一个容器 + 一个预览端口

首实现 WorktreeDockerProvider；将来 K8sProvider 不动上层。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class RepoSpec:
    name: str
    url: str
    default_branch: str = "main"
    pat: str | None = None


@dataclass
class WorkspaceHandle:
    id: str
    run_id: str
    project_id: str
    root: Path
    branch: str
    repos: dict[str, str] = field(default_factory=dict)   # {repo_name: worktree 路径}
    port: int | None = None
    container_id: str | None = None
    image: str | None = None


@dataclass
class ExecResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class WorkspaceError(RuntimeError):
    """本层失败一律以此暴露，不让 subprocess / docker 的原始异常泄漏。"""


@runtime_checkable
class WorkspaceProvider(Protocol):
    async def acquire(self, *, project_id: str, run_id: str, branch: str,
                      base_branch: str, repos: list[RepoSpec]) -> WorkspaceHandle: ...

    async def release(self, ws: WorkspaceHandle) -> None: ...

    async def exec(self, ws: WorkspaceHandle, argv: list[str], *,
                   cwd: str | None = None, timeout: float | None = None) -> ExecResult: ...
