import subprocess
from pathlib import Path

import pytest

from orchestrator.git_manager import GitConflictError, GitManager


@pytest.fixture
def temp_repo(tmp_path) -> Path:
    """一个有 main 分支 + 一个初始提交的临时 git 仓库。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "test@test")
    run("config", "user.name", "test")
    (repo / "file.txt").write_text("line1\n")
    run("add", "file.txt")
    run("commit", "-m", "init")
    return repo


def test_create_branch_from_main(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    branches = subprocess.run(
        ["git", "branch"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "cr/abc" in branches


def test_commit_all_on_branch(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    (temp_repo / "file.txt").write_text("line1\nline2\n")
    sha = gm.commit_all("cr/abc", "cr: change")
    assert sha
    log = subprocess.run(
        ["git", "log", "--oneline", "cr/abc"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "cr: change" in log


def test_has_changes(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    assert gm.has_changes("cr/abc") is False
    (temp_repo / "file.txt").write_text("changed\n")
    assert gm.has_changes("cr/abc") is True


def test_merge_clean(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    (temp_repo / "new.txt").write_text("brand new\n")
    gm.commit_all("cr/abc", "cr: add new file")
    gm.merge_to_main("cr/abc")
    main_files = subprocess.run(
        ["git", "ls-tree", "--name-only", "main"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "new.txt" in main_files


def test_merge_conflict_raises(temp_repo):
    gm = GitManager(str(temp_repo))
    # 分支改 file.txt
    gm.create_branch("cr/abc")
    (temp_repo / "file.txt").write_text("branch version\n")
    gm.commit_all("cr/abc", "cr: branch edit")
    # main 也改 file.txt（制造冲突）
    run = lambda *a: subprocess.run(["git", *a], cwd=temp_repo, check=True, capture_output=True)
    run("checkout", "main")
    (temp_repo / "file.txt").write_text("main version\n")
    run("add", "file.txt")
    run("commit", "-m", "main edit")
    with pytest.raises(GitConflictError):
        gm.merge_to_main("cr/abc")
    # 冲突后仓库应回到干净的 main（不留半合并状态）
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert status.strip() == ""


def test_merge_with_dirty_working_tree(temp_repo):
    """回归：build 阶段在 commit_all 之后产生的 dist / tsbuildinfo 等
    artifact 把工作树搞脏，旧版 merge_to_main rebase 会拒绝 ('unstaged changes')，
    误报为 GitConflictError。现在应该 stash -u 跳过，merge 完 drop stash。
    """
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    (temp_repo / "real_change.txt").write_text("from cr branch\n")
    gm.commit_all("cr/abc", "cr: real change")
    # 模拟 build artifact：未跟踪的 dist + 改过但未 stage 的 file.txt
    (temp_repo / "file.txt").write_text("line1\ndirty\n")  # tracked & modified
    (temp_repo / "dist_artifact.js").write_text("// build output\n")  # untracked
    # 应该成功合并而不是 conflict
    gm.merge_to_main("cr/abc")
    main_files = subprocess.run(
        ["git", "ls-tree", "--name-only", "main"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "real_change.txt" in main_files
    # stash 已被 drop（不留残留）
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert stash_list.strip() == ""


def test_delete_branch(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    gm.delete_branch("cr/abc")
    branches = subprocess.run(
        ["git", "branch"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "cr/abc" not in branches
