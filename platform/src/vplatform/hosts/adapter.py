"""GitHostAdapter（D10 §10.3）—— 只实现 GitHub，但接缝现在就立起来。

**为什么这条要写成硬约束**：v1 的 UI 文案承诺「支持 GitHub / Gitee / 云效」，
代码却在 `github_client.py:72` 对非 GitHub URL 直接 `raise ValueError` ——
因为根本没有接缝，承诺无处落地。

命名刻意中性：GitHub 叫 PR、云效叫合并请求 —— 核心层不认这两个词，只认 ChangeRef。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ChangeRef:
    """GitHub 的 PR / 云效的合并请求 / Gitee 的 PR 的中性表示。"""
    id: str
    number: int | None
    url: str


class HostError(RuntimeError):
    pass


@runtime_checkable
class GitHostAdapter(Protocol):
    async def clone(self, url: str, dest: Path, *, bare: bool = False,
                    pat: str | None = None) -> None: ...

    async def fetch(self, work_dir: Path) -> None: ...

    async def push(self, work_dir: Path, branch: str, *, pat: str | None = None) -> None: ...

    async def open_change(self, *, repo_url: str, head: str, base: str, title: str,
                          body: str, pat: str | None = None) -> ChangeRef: ...

    async def comment(self, change: ChangeRef, body: str, *,
                      pat: str | None = None) -> None: ...

    def verify_webhook(self, headers: dict, raw: bytes, *, secret: str) -> bool: ...
