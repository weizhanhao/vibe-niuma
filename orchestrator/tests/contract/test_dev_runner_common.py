"""DevRunner 共用层契约测试 —— 用 tmp_path 临时 git 仓库验证纯逻辑。"""
import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.impl._dev_runner_common import (
    collect_log,
    commit_all,
    has_changes,
    make_run_result,
)


@pytest.fixture
def tmp_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "f.txt").write_text("a\n")
    run("add", "f.txt")
    run("commit", "-m", "init")
    run("checkout", "-b", "cr/x")
    return repo


def test_has_changes_false_on_clean_branch(tmp_repo):
    assert has_changes(str(tmp_repo), "cr/x") is False


def test_has_changes_true_when_working_tree_dirty(tmp_repo):
    (tmp_repo / "f.txt").write_text("changed\n")
    assert has_changes(str(tmp_repo), "cr/x") is True


def test_has_changes_true_when_branch_has_new_commit(tmp_repo):
    (tmp_repo / "n.txt").write_text("new\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-m", "n"], cwd=tmp_repo, check=True)
    assert has_changes(str(tmp_repo), "cr/x") is True


def test_commit_all_returns_sha_and_creates_commit(tmp_repo):
    (tmp_repo / "f.txt").write_text("v2\n")
    sha = commit_all(str(tmp_repo), "cr/x", "cr: edit")
    assert sha
    log = subprocess.run(
        ["git", "log", "--oneline", "cr/x"], cwd=tmp_repo, capture_output=True, text=True,
    ).stdout
    assert "cr: edit" in log


def test_commit_all_no_op_when_clean_returns_head(tmp_repo):
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_repo, capture_output=True, text=True,
    ).stdout.strip()
    sha = commit_all(str(tmp_repo), "cr/x", "noop")
    assert sha == head_before


def test_collect_log_truncates_long_input():
    long = "x" * 20000
    result = collect_log(long, "", limit=1000)
    assert len(result) <= 1100  # 含截断标头
    assert "truncated" in result


def test_make_run_result_changed_true_when_diff(tmp_repo):
    (tmp_repo / "g.txt").write_text("new\n")
    res = make_run_result(str(tmp_repo), "cr/x", "ran ok", "cr: change")
    assert res.changed is True
    assert res.commit_sha


def test_make_run_result_changed_false_when_clean(tmp_repo):
    res = make_run_result(str(tmp_repo), "cr/x", "ran ok", "cr: change")
    assert res.changed is False
    assert res.commit_sha is None
    assert "no changes" in res.log
