"""GitManager —— 对目标仓库做真实 git 操作。所有方法同步（subprocess）；
Pipeline 在 async 上下文里用 asyncio.to_thread 调用它们。
"""
import subprocess


class GitConflictError(Exception):
    """rebase/merge 出现冲突。"""


class GitManager:
    def __init__(self, repo_path: str):
        self._repo = repo_path

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self._repo,
            check=check,
            capture_output=True,
            text=True,
        )

    def create_branch(self, branch: str) -> None:
        """从 main 切一个新分支并切过去。"""
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
        """
        self._git("checkout", branch)
        rebase = self._git("rebase", "main", check=False)
        if rebase.returncode != 0:
            self._git("rebase", "--abort", check=False)
            self._git("checkout", "main")
            raise GitConflictError(f"rebase conflict: {rebase.stdout}{rebase.stderr}")
        self._git("checkout", "main")
        merge = self._git("merge", "--ff-only", branch, check=False)
        if merge.returncode != 0:
            raise GitConflictError(f"merge failed: {merge.stdout}{merge.stderr}")

    def delete_branch(self, branch: str) -> None:
        """删除分支（强制）。先确保不在该分支上。"""
        self._git("checkout", "main")
        self._git("branch", "-D", branch, check=False)
