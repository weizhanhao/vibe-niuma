"""GitManager —— 对目标仓库做真实 git 操作。所有方法同步（subprocess）；
Pipeline 在 async 上下文里用 asyncio.to_thread 调用它们。

Plan 8：repo_path 既可指单仓 root，也可指「多仓项目根」（顶层 N 个含 .git 的子目录）。
单仓时所有方法用本地实现；多仓时遍历子仓 / 委托给 multi_repo.merge_to_main_atomic。
"""
import asyncio
import subprocess
from pathlib import Path

from orchestrator.multi_repo import (
    GitConflictError as MultiRepoConflictError,
    discover_sub_repos,
    merge_to_main_atomic,
)


class GitConflictError(Exception):
    """rebase/merge 出现冲突。"""


class GitManager:
    def __init__(self, repo_path: str):
        self._repo = repo_path
        # Plan 8：若顶层含子仓，sub_repos 非空 → 走多仓路径
        self._sub_repos: list[Path] = discover_sub_repos(Path(repo_path))

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self._repo,
            check=check,
            capture_output=True,
            text=True,
        )

    def create_branch(self, branch: str) -> None:
        """从 main 切一个新分支并切过去。

        防御：上一个 CR 的 pipeline 可能在 dev_runner 写完文件还没 commit 时
        被 orchestrator restart / crash 打断，工作树留下未提交改动 + 留在
        feature branch 上。直接 `git checkout main` 会被 git 拒绝（dirty work
        tree）。这里先 stash + clean 把工作区强制回到 main 干净态再切。
        stash 不丢，可以 `git stash list` 找回。
        """
        status = self._git("status", "--porcelain").stdout
        if status.strip():
            self._git(
                "stash", "push", "--include-untracked",
                "-m", f"vibe-niuma-autoclean-before-{branch}",
                check=False,
            )
        # 仍然 dirty（stash 失败 / 有 submodule 未跟踪）→ 强制 reset + clean
        residual = self._git("status", "--porcelain").stdout
        if residual.strip():
            self._git("reset", "--hard", "HEAD", check=False)
            self._git("clean", "-fd", check=False)
        self._git("checkout", "main")
        self._git("checkout", "-b", branch)

    def has_changes(self, branch: str) -> bool:
        """branch 的工作树相对 HEAD 是否有未提交改动。"""
        self._git("checkout", branch)
        result = self._git("status", "--porcelain")
        return bool(result.stdout.strip())

    def commit_all(self, branch: str, message: str) -> str:
        """在 branch 上 add -A 并提交，返回 commit SHA。"""
        self._git("checkout", branch)
        self._git("add", "-A")
        self._git("commit", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def merge_to_main(self, branch: str) -> None:
        """先把 branch rebase 到最新 main，再 fast-forward 合并进 main。
        rebase 或 merge 冲突 → 回滚到干净状态并抛 GitConflictError。

        Plan 8：多仓项目（self._sub_repos 非空）→ 委托给 merge_to_main_atomic，
        全 N 仓「要么都成功要么都回滚」。单仓沿用本地两阶段实现。
        """
        if self._sub_repos:
            try:
                asyncio.run(merge_to_main_atomic(self._sub_repos, branch))
            except MultiRepoConflictError as exc:
                raise GitConflictError(str(exc)) from exc
            return

        status = self._git("status", "--porcelain").stdout
        stashed = False
        if status.strip():
            self._git("stash", "push", "-u", "-m", "vibe-niuma-merge-prep")
            stashed = True
        try:
            self._git("checkout", branch)
            rebase = self._git("rebase", "main", check=False)
            if rebase.returncode != 0:
                self._git("rebase", "--abort", check=False)
                self._git("checkout", "main")
                raise GitConflictError(
                    f"rebase conflict: {rebase.stdout}{rebase.stderr}"
                )
            self._git("checkout", "main")
            merge = self._git("merge", "--ff-only", branch, check=False)
            if merge.returncode != 0:
                raise GitConflictError(
                    f"merge failed: {merge.stdout}{merge.stderr}"
                )
        finally:
            if stashed:
                # build artifact stash 不再需要 —— 直接 drop。
                self._git("stash", "drop", check=False)

    def delete_branch(self, branch: str) -> None:
        """删除分支（强制）。先确保不在该分支上。"""
        self._git("checkout", "main")
        self._git("branch", "-D", branch, check=False)
