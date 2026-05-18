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


def test_create_branch_recovers_from_dirty_feature_branch(temp_repo):
    """业务员的真实复现：上一个 CR pipeline 跑到一半被 orchestrator restart 打断，
    工作区留着未提交改动 + 残留 untracked 文件，HEAD 还在 feature branch。
    新 CR create_branch 应该自动 stash + 切回 main + 再开新 branch，
    而不是 git-error。"""
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/old")
    (temp_repo / "file.txt").write_text("dirty modify\n")
    (temp_repo / "leftover.txt").write_text("untracked from old CR\n")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=temp_repo, capture_output=True, text=True,
    ).stdout
    assert status.strip(), "前置：工作区必须是 dirty"

    # 现在新 CR 进来 —— 应该不抛
    gm.create_branch("cr/new")

    # 验证：现在 HEAD 是 cr/new，工作区干净
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=temp_repo, capture_output=True, text=True,
    ).stdout.strip()
    assert head == "cr/new"
    status2 = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=temp_repo, capture_output=True, text=True,
    ).stdout
    assert not status2.strip(), "新分支创建后工作区应干净"


def test_create_branch_preserves_stash_for_recovery(temp_repo):
    """defensive 清理产物存 stash，business 想找回还能找到。"""
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/old")
    (temp_repo / "file.txt").write_text("important uncommitted\n")
    gm.create_branch("cr/new")

    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=temp_repo, capture_output=True, text=True,
    ).stdout
    assert "vibe-niuma-autoclean-before-cr/new" in stash_list


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
