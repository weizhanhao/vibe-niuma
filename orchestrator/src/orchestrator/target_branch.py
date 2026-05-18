"""target_branch —— 业务员 CR 的合并目标分支管理。

Plan 11 · M1.T5.

设计：
- 业务员的所有 CR 最终汇聚到一个**专用分支**（默认 'vibe-niuma/dev'），
  而不是直接合到客户的 main 上。程序员从这条分支提 PR review 后再合到 main。
- 这样：业务员永远不污染客户主分支，程序员有 review 闸门。
- 首次对一个仓做 sync 时，若 remote 没这条分支就：
  `checkout -b <targetBranch> <mainBranch> && push -u origin <targetBranch>`

幂等性：
- 第二次调用同一仓同一分支 → 检测到 remote 已有就跳过，不报错。
- 网络抖动 / push 失败时抛 GitOperationError，调用方决定 retry。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from orchestrator.github_client import (
    GitOperationError,
    _run_git,
    push,
    remote_branch_exists,
)

logger = logging.getLogger(__name__)


def _checkout_new_branch_from(repo_dir: Path, new_branch: str, source: str) -> None:
    """git checkout -b <new_branch> origin/<source>。

    source 可以是本地分支名或 'origin/main' 形式。这里强制从远端 ref 切，
    避免本地 main 滞后于远端的情况。
    """
    cmd = ["git", "checkout", "-b", new_branch, f"origin/{source}"]
    _run_git(cmd, cwd=repo_dir)


def ensure_target_branch(
    repo_dir: Path,
    *,
    main_branch: str,
    target_branch: str,
    pat: Optional[str] = None,
) -> bool:
    """确保 remote 上有 target_branch；没有就从 main_branch 切出来 + push -u。

    返回 True = 这次新建并 push 了；False = remote 早就有，没动。

    前置：repo_dir 是已经 clone + 至少 fetch 过一次的工作目录或 worktree。
    """
    if remote_branch_exists(repo_dir, target_branch, pat=pat):
        logger.info("ensure_target_branch: %s 已存在 remote，跳过", target_branch)
        return False

    # 确认 main_branch 在 remote 上能找到 —— 不存在就不能从它切
    if not remote_branch_exists(repo_dir, main_branch, pat=pat):
        raise GitOperationError(
            cmd=["ensure_target_branch"],
            rc=1,
            stderr=(
                f"main 分支 'origin/{main_branch}' 在 remote 上找不到。"
                f"业务员可能在项目 wizard 里把 mainBranch 配错了。"
            ),
        )

    logger.info(
        "ensure_target_branch: 从 origin/%s 切 %s 并 push -u",
        main_branch,
        target_branch,
    )
    # 切之前 stash 防 dirty work tree
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    if status.strip():
        subprocess.run(
            ["git", "stash", "push", "--include-untracked",
             "-m", f"vibe-niuma-stash-before-create-{target_branch}"],
            cwd=str(repo_dir),
            check=False,
            capture_output=True,
        )

    _checkout_new_branch_from(repo_dir, target_branch, main_branch)
    push(repo_dir, target_branch, pat=pat, set_upstream=True)
    return True
