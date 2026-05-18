"""multi_repo —— 跨多个子仓的两阶段原子合并。

设计要点（来自 Plan 8 spec）：
- 项目 = 顶层文件夹下 N 个含 .git/ 的子目录（典型：frontend/.git + backend/.git）
- discover_sub_repos: 扫顶层，凡含 .git 的都是子仓
- merge_to_main_atomic: 两阶段
    Phase 1 dry-rebase 所有子仓
    Phase 2 ff-merge 所有子仓
  任一阶段任一仓失败 → git update-ref 把已成功的子仓回滚 → 抛 GitConflictError
- update-ref 只动 ref，不动工作树 —— 业务员未提交的本地改动不丢
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

LogSink = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class RepoState:
    path: Path
    branch_name: str        # e.g. "cr/abc123"
    cr_sha_before: str      # pre-rebase cr ref
    main_sha_before: str    # pre-merge main ref
    stashed: bool


class GitConflictError(RuntimeError):
    """Phase 1 或 Phase 2 失败。message 含具体仓名 + git stderr。"""


# ── 子仓发现 ─────────────────────────────────────────────────────
def discover_sub_repos(project_path: Path) -> list[Path]:
    """扫 project_path 顶层一级目录，凡含 .git/（dir 或 file，git-worktree）的收集。
    按 name 字典序排序，保证 rebase / merge 顺序可预期。"""
    if not project_path.is_dir():
        return []
    out: list[Path] = []
    for entry in sorted(project_path.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if (entry / ".git").exists():
            out.append(entry)
    return out


# ── 底层 git 包装 ────────────────────────────────────────────────
def _run(repo: Path, *args: str, check: bool = False) -> tuple[int, str]:
    r = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    out = (r.stdout or "") + (r.stderr or "")
    if check and r.returncode != 0:
        raise GitConflictError(f"{repo.name}: git {' '.join(args)}\n{out}")
    return r.returncode, out


def _rev_parse(repo: Path, ref: str) -> str:
    rc, out = _run(repo, "rev-parse", ref, check=True)
    return out.strip()


def _checkout(repo: Path, ref: str) -> None:
    _run(repo, "checkout", ref, check=True)


def _rebase(repo: Path, onto: str) -> tuple[int, str]:
    return _run(repo, "rebase", onto)


def _rebase_abort(repo: Path) -> None:
    _run(repo, "rebase", "--abort")


def _merge_ff_only(repo: Path, branch: str) -> tuple[int, str]:
    return _run(repo, "merge", "--ff-only", branch)


def _update_ref(repo: Path, ref: str, sha: str) -> None:
    _run(repo, "update-ref", ref, sha, check=True)


def _stash_if_dirty(repo: Path) -> bool:
    """工作树脏才 stash —— clean tree 会 stash 出空条目，浪费 + 后续 drop 报错。"""
    rc, out = _run(repo, "status", "--porcelain")
    if not out.strip():
        return False
    _run(repo, "stash", "push", "-u", "-m", "vibe-niuma-multi-repo-merge", check=True)
    return True


def _stash_drop(repo: Path) -> None:
    _run(repo, "stash", "drop")


# ── 两阶段原子合并 ──────────────────────────────────────────────
async def _emit(log: Optional[LogSink], line: str) -> None:
    if log is not None:
        await log(line)


async def merge_to_main_atomic(
    sub_repos: list[Path],
    branch: str,
    *,
    log: Optional[LogSink] = None,
) -> None:
    """两阶段原子合并 N 个子仓。任一阶段任一仓失败 → 回滚 + 抛 GitConflictError。
    成功路径：每仓 ✓ merged；finally drop stash（build artifact 是临时产物）。"""
    states: list[RepoState] = []
    for repo in sub_repos:
        stashed = _stash_if_dirty(repo)
        cr_sha = _rev_parse(repo, branch)
        main_sha = _rev_parse(repo, "main")
        states.append(RepoState(repo, branch, cr_sha, main_sha, stashed))

    try:
        # ── Phase 1: dry-rebase ────────────────────────────────────
        rebased: list[RepoState] = []
        for s in states:
            await _emit(log, f"[{s.path.name}] git rebase main")
            _checkout(s.path, branch)
            rc, out = _rebase(s.path, "main")
            if rc != 0:
                _rebase_abort(s.path)
                for done in rebased:
                    _update_ref(done.path, f"refs/heads/{branch}", done.cr_sha_before)
                    await _emit(log, f"[{done.path.name}] ↩ 已回滚 cr 分支到 {done.cr_sha_before[:8]}")
                raise GitConflictError(f"{s.path.name}: rebase 冲突\n{out}")
            rebased.append(s)

        # ── Phase 2: ff-merge ──────────────────────────────────────
        merged: list[RepoState] = []
        for s in states:
            await _emit(log, f"[{s.path.name}] git merge --ff-only {branch}")
            _checkout(s.path, "main")
            rc, out = _merge_ff_only(s.path, branch)
            if rc != 0:
                # 回滚已 merged 的 main，再回滚所有 cr
                for done in merged:
                    _update_ref(done.path, "refs/heads/main", done.main_sha_before)
                for s2 in states:
                    _update_ref(s2.path, f"refs/heads/{branch}", s2.cr_sha_before)
                raise GitConflictError(f"phase 2 unexpected: {s.path.name}\n{out}")
            merged.append(s)
            await _emit(log, f"[{s.path.name}] ✓ merged")

    finally:
        for s in states:
            if s.stashed:
                _stash_drop(s.path)
