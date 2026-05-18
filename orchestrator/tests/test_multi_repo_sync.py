"""multi_repo_sync 测试 —— 用本地 bare repo 模拟 GitHub，验证：
1. 首次 sync clones + 创建 target_branch
2. 二次 sync 是 fetch（不重 clone）
3. 多仓并行
4. 坏 URL 不拖死好仓
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.github_client import remote_branch_exists
from orchestrator.multi_repo_sync import (
    RepoSpec,
    sync_one,
    sync_repos,
)


def _seed_bare(path: Path, *, branch: str = "main") -> Path:
    """搭一个 bare repo + 推一个 seed commit。"""
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    work = path.parent / f"_seed_{path.name}"
    subprocess.run(["git", "clone", str(path), str(work)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True, capture_output=True)
    (work / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=work, check=True, capture_output=True)
    current = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=work, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if current != branch:
        subprocess.run(["git", "branch", "-M", branch], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", branch], cwd=work, check=True, capture_output=True)
    return path


@pytest.fixture(autouse=True)
def _patch_parse_github_url(monkeypatch):
    """multi_repo_sync 用 parse_github_url 拿 repo_name；测试里 URL 是 file://，
    跳过校验直接从路径末段拿 repo 名。"""
    from orchestrator import github_client, multi_repo_sync

    def fake_parse(url: str) -> tuple[str, str]:
        # file:///tmp/xxx/some-repo.git → ('local', 'some-repo')
        name = Path(url.replace("file://", "")).stem
        return ("local", name)

    monkeypatch.setattr(github_client, "parse_github_url", fake_parse)
    monkeypatch.setattr(multi_repo_sync, "parse_github_url", fake_parse)


@pytest.mark.asyncio
async def test_sync_one_first_time_clones_and_creates_target(tmp_path):
    remote = _seed_bare(tmp_path / "remote.git", branch="main")
    workspaces = tmp_path / "workspaces"

    spec = RepoSpec(url=f"file://{remote}", main_branch="main", target_branch="vibe-niuma/dev")
    result = await sync_one(spec, project_id="proj-1", pat=None, workspaces_root=workspaces)

    assert result.name == "remote"
    assert result.target_branch == "vibe-niuma/dev"
    assert result.target_branch_created is True
    assert (workspaces / "proj-1" / "remote" / ".git").is_dir()
    # remote 上应该真有 vibe-niuma/dev 了
    assert remote_branch_exists(Path(result.work_dir), "vibe-niuma/dev") is True


@pytest.mark.asyncio
async def test_sync_one_second_time_is_idempotent(tmp_path):
    remote = _seed_bare(tmp_path / "remote.git", branch="main")
    workspaces = tmp_path / "workspaces"
    spec = RepoSpec(url=f"file://{remote}", main_branch="main", target_branch="vibe-niuma/dev")

    first = await sync_one(spec, project_id="proj-1", pat=None, workspaces_root=workspaces)
    assert first.target_branch_created is True

    # 第二次：work_dir 已存在 → fetch；target_branch 已存在 → 不重创
    second = await sync_one(spec, project_id="proj-1", pat=None, workspaces_root=workspaces)
    assert second.target_branch_created is False
    assert second.work_dir == first.work_dir


@pytest.mark.asyncio
async def test_sync_repos_parallel_with_mixed_success_and_failure(tmp_path):
    good_remote = _seed_bare(tmp_path / "good.git", branch="main")
    workspaces = tmp_path / "workspaces"

    specs = [
        RepoSpec(url=f"file://{good_remote}", main_branch="main", target_branch="vibe-niuma/dev"),
        RepoSpec(url=f"file://{tmp_path}/nonexistent.git", main_branch="main", target_branch="vibe-niuma/dev"),
    ]
    result = await sync_repos(specs, project_id="proj-2", pat=None, workspaces_root=workspaces)

    assert len(result.synced) == 1
    assert len(result.failed) == 1
    assert result.synced[0].name == "good"
    assert result.failed[0].url.endswith("nonexistent.git")
    # 坏 URL 错误分类应该是 git_op（clone 失败）
    assert result.failed[0].error_kind in {"git_op", "unknown"}


@pytest.mark.asyncio
async def test_sync_repos_empty_list_returns_empty_result(tmp_path):
    workspaces = tmp_path / "workspaces"
    result = await sync_repos([], project_id="proj-3", pat=None, workspaces_root=workspaces)
    assert result.synced == []
    assert result.failed == []


@pytest.mark.asyncio
async def test_sync_one_with_non_default_main_branch(tmp_path):
    """客户 main 叫 master，target 用默认 vibe-niuma/dev。"""
    remote = _seed_bare(tmp_path / "master-repo.git", branch="master")
    workspaces = tmp_path / "workspaces"
    spec = RepoSpec(url=f"file://{remote}", main_branch="master", target_branch="vibe-niuma/dev")

    result = await sync_one(spec, project_id="proj-4", pat=None, workspaces_root=workspaces)
    assert result.target_branch_created is True
    assert remote_branch_exists(Path(result.work_dir), "vibe-niuma/dev") is True
