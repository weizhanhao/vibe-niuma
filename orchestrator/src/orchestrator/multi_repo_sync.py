"""multi_repo_sync —— 业务员配的 N 个 GitHub 仓的「按需 clone + fetch + 维护
target_branch」。

Plan 11 · M1.T4.

布局：
    <workspaces_root>/<project_id>/<repo_name>/   (full clone, working tree)

每个仓一个 full clone（**不用 bare + worktree**，简化）。CR pipeline 后续在
这个工作目录里切 cr/<id> 分支干活、merge 时 push target_branch 到 origin。

单仓 sync 流程（同步部分跑在 to_thread 里）：
1. 算 work_dir = workspaces_root/project_id/<repo_name>
2. work_dir 不存在 → clone url → work_dir + 配 git user
3. work_dir 存在 → fetch origin
4. ensure_target_branch（必要时从 main 切 + push）
5. checkout target_branch + reset --hard origin/target_branch
6. 返回 {name, work_dir, head_sha, target_branch_created}

异常：
- GitHubError 子类（AuthError / GitOperationError 等）会被 catch 包成
  SyncResult.failed[i]，**不让一个坏仓拖死整批**。
- 完全空 repos 列表 → 直接返回空 result（noop，业务员还没绑仓）。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from orchestrator.github_client import (
    GitHubError,
    clone,
    fetch,
    parse_github_url,
)
from orchestrator.target_branch import ensure_target_branch

logger = logging.getLogger(__name__)


# ── 数据契约 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RepoSpec:
    """业务员在 wizard 里填的一行：URL + 两个分支名。"""
    url: str
    main_branch: str = "main"
    target_branch: str = "vibe-niuma/dev"


@dataclass
class SyncedRepo:
    """成功 sync 一个仓的结果。"""
    name: str
    url: str
    work_dir: str
    head_sha: str
    target_branch: str
    target_branch_created: bool   # 这次是不是新创建并 push 的


@dataclass
class FailedRepo:
    """失败一个仓的结果（含原始 URL + 错误类别 + 错误消息）。"""
    url: str
    error_kind: str   # 'auth' / 'not_found' / 'git_op' / 'unknown'
    error_message: str


@dataclass
class SyncResult:
    synced: list[SyncedRepo] = field(default_factory=list)
    failed: list[FailedRepo] = field(default_factory=list)


# ── 同步实现（subprocess，跑在 to_thread 里） ──────────────────────


def _run(cmd: list[str], cwd: Path) -> str:
    """跑一条 git，rc != 0 抛 GitOperationError；正常返回 stdout。"""
    from orchestrator.github_client import GitOperationError
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitOperationError(cmd, proc.returncode, proc.stderr)
    return proc.stdout


def _set_local_git_identity(repo_dir: Path) -> None:
    """给本地 repo 设 user.email / user.name，避免 commit 时报「Please tell me who you are」。

    用户身份在这里固定为通用的 vibe-niuma noreply —— 真正的 commit 作者会在
    pipeline 提交时再 override（带业务员名字）。
    """
    _run(["git", "config", "user.email", "vibe-niuma@noreply.local"], cwd=repo_dir)
    _run(["git", "config", "user.name", "vibe-niuma"], cwd=repo_dir)


def _sync_one_sync(
    spec: RepoSpec,
    *,
    project_id: str,
    pat: Optional[str],
    workspaces_root: Path,
) -> SyncedRepo:
    """单仓 sync —— 全同步（跑 git CLI 用 subprocess）。"""
    _owner, repo_name = parse_github_url(spec.url)
    project_root = workspaces_root / project_id
    work_dir = project_root / repo_name

    if not work_dir.exists():
        logger.info("sync %s → clone 到 %s", spec.url, work_dir)
        clone(spec.url, work_dir, pat=pat)
        _set_local_git_identity(work_dir)
    else:
        logger.info("sync %s → 已存在，git fetch", spec.url)
        fetch(work_dir, pat=pat)

    # 确保 target_branch 在 remote 上有；没有就切 + push
    created = ensure_target_branch(
        work_dir,
        main_branch=spec.main_branch,
        target_branch=spec.target_branch,
        pat=pat,
    )

    # 把工作树切到 target_branch + 强制对齐 remote
    _run(["git", "checkout", spec.target_branch], cwd=work_dir)
    _run(["git", "reset", "--hard", f"origin/{spec.target_branch}"], cwd=work_dir)

    head_sha = _run(["git", "rev-parse", "HEAD"], cwd=work_dir).strip()

    return SyncedRepo(
        name=repo_name,
        url=spec.url,
        work_dir=str(work_dir),
        head_sha=head_sha,
        target_branch=spec.target_branch,
        target_branch_created=created,
    )


def _classify_error(exc: Exception) -> str:
    """把异常归到几类，给前端 UI 显示不同提示用。"""
    from orchestrator.github_client import (
        AuthError,
        GitOperationError,
        NotFoundError,
        RateLimitError,
    )
    if isinstance(exc, AuthError):
        return "auth"
    if isinstance(exc, NotFoundError):
        return "not_found"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if isinstance(exc, GitOperationError):
        return "git_op"
    if isinstance(exc, GitHubError):
        return "github"
    if isinstance(exc, ValueError):
        return "bad_url"
    return "unknown"


# ── async 公开接口 ──────────────────────────────────────────────────


async def sync_one(
    spec: RepoSpec,
    *,
    project_id: str,
    pat: Optional[str] = None,
    workspaces_root: Path,
) -> SyncedRepo:
    """单仓 async wrapper —— 实际跑在线程池里。"""
    return await asyncio.to_thread(
        _sync_one_sync,
        spec,
        project_id=project_id,
        pat=pat,
        workspaces_root=workspaces_root,
    )


async def sync_repos(
    specs: list[RepoSpec],
    *,
    project_id: str,
    pat: Optional[str] = None,
    workspaces_root: Path,
) -> SyncResult:
    """并行 sync 多个仓。坏仓不影响好仓。"""
    result = SyncResult()
    if not specs:
        return result

    # asyncio.gather 加 return_exceptions=True：每个 coroutine 单独算结果
    coros = [
        sync_one(spec, project_id=project_id, pat=pat, workspaces_root=workspaces_root)
        for spec in specs
    ]
    outcomes = await asyncio.gather(*coros, return_exceptions=True)
    for spec, outcome in zip(specs, outcomes):
        if isinstance(outcome, Exception):
            logger.exception("sync %s 失败", spec.url, exc_info=outcome)
            result.failed.append(
                FailedRepo(
                    url=spec.url,
                    error_kind=_classify_error(outcome),
                    error_message=str(outcome),
                )
            )
        else:
            result.synced.append(outcome)
    return result
