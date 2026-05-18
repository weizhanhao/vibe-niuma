"""target_branch 测试 —— 用本地 bare repo 当 fake remote 跑真 git 命令。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.github_client import GitOperationError, clone, remote_branch_exists
from orchestrator.target_branch import ensure_target_branch


# 复用 test_github_client.py 那个 helper 思路，但局部独立避免跨文件 fixture 耦合
def _init_bare_with_main(path: Path, *, branch: str = "main") -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    work = path.parent / f"_seed_{path.name}"
    subprocess.run(["git", "clone", str(path), str(work)], check=True, capture_output=True)
    (work / "README.md").write_text("seed\n")
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=work, check=True, capture_output=True)
    # 强制把当前分支重命名为指定 branch（处理系统默认是 master 的情况）
    current = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=work, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if current != branch:
        subprocess.run(["git", "branch", "-M", branch], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", branch], cwd=work, check=True, capture_output=True)
    return path


def test_ensure_target_branch_creates_when_missing(tmp_path):
    remote = _init_bare_with_main(tmp_path / "remote.git", branch="main")
    local = tmp_path / "local"
    clone(f"file://{remote}", local)
    # 给本地配下 user 才能后续 push
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=local, check=True, capture_output=True)

    # 这次应该创建并 push
    created = ensure_target_branch(local, main_branch="main", target_branch="vibe-niuma/dev")
    assert created is True
    assert remote_branch_exists(local, "vibe-niuma/dev") is True


def test_ensure_target_branch_idempotent(tmp_path):
    remote = _init_bare_with_main(tmp_path / "remote.git", branch="main")
    local = tmp_path / "local"
    clone(f"file://{remote}", local)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=local, check=True, capture_output=True)

    # 首次创建
    assert ensure_target_branch(local, main_branch="main", target_branch="vibe-niuma/dev") is True
    # 第二次：已存在 → 应返 False，不报错
    assert ensure_target_branch(local, main_branch="main", target_branch="vibe-niuma/dev") is False


def test_ensure_target_branch_supports_non_default_main(tmp_path):
    """客户 main 不一定叫 'main'，可能是 'master' / 'trunk' / 'release/main'。"""
    remote = _init_bare_with_main(tmp_path / "remote.git", branch="master")
    local = tmp_path / "local"
    clone(f"file://{remote}", local)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=local, check=True, capture_output=True)

    created = ensure_target_branch(local, main_branch="master", target_branch="vibe-niuma/dev")
    assert created is True
    assert remote_branch_exists(local, "vibe-niuma/dev") is True


def test_ensure_target_branch_raises_when_main_missing(tmp_path):
    """业务员 wizard 配错 mainBranch（叫 'main' 但客户其实是 'master'）→ 给清晰错误。"""
    remote = _init_bare_with_main(tmp_path / "remote.git", branch="master")
    local = tmp_path / "local"
    clone(f"file://{remote}", local)

    with pytest.raises(GitOperationError, match="origin/main.*找不到"):
        ensure_target_branch(local, main_branch="main", target_branch="vibe-niuma/dev")
