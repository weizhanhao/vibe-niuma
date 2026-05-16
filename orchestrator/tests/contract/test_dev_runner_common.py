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


# ── Plan 8 Task 7：多仓 has_changes / commit_all / make_run_result ─────
@pytest.fixture
def multi_repo_project(tmp_path) -> Path:
    """project/ 下含 frontend/.git + backend/.git；各自 main + cr/x 分支。"""
    project = tmp_path / "project"
    project.mkdir()
    for name in ["frontend", "backend"]:
        repo = project / name
        repo.mkdir()
        run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
        run("init", "-b", "main")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (repo / "README.md").write_text(f"# {name}\n")
        run("add", "-A")
        run("commit", "-m", "init")
        run("checkout", "-b", "cr/x")
    return project


def test_has_changes_true_if_any_sub_repo_dirty(multi_repo_project: Path):
    (multi_repo_project / "frontend" / "file.txt").write_text("change\n")
    assert has_changes(str(multi_repo_project), "cr/x") is True


def test_has_changes_false_when_all_sub_repos_clean(multi_repo_project: Path):
    assert has_changes(str(multi_repo_project), "cr/x") is False


def test_commit_all_walks_sub_repos(multi_repo_project: Path):
    (multi_repo_project / "frontend" / "feature.txt").write_text("F\n")
    (multi_repo_project / "backend" / "api.py").write_text("def x(): pass\n")
    result = commit_all(str(multi_repo_project), "cr/x", "multi-repo cr")
    assert isinstance(result, dict)
    assert set(result.keys()) == {"frontend", "backend"}
    assert all(isinstance(sha, str) and len(sha) >= 7 for sha in result.values())


def test_commit_all_skips_clean_sub_repo(multi_repo_project: Path):
    (multi_repo_project / "frontend" / "feature.txt").write_text("F\n")
    result = commit_all(str(multi_repo_project), "cr/x", "only frontend")
    assert isinstance(result, dict)
    head_backend = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(multi_repo_project / "backend"),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert result["backend"] == head_backend  # 干净子仓返回 HEAD（无新 commit）


def test_make_run_result_returns_dict_of_commit_shas(multi_repo_project: Path):
    (multi_repo_project / "frontend" / "f.txt").write_text("change\n")
    (multi_repo_project / "backend" / "b.txt").write_text("change\n")
    res = make_run_result(str(multi_repo_project), "cr/x", "ok", "multi cr")
    assert res.changed is True
    assert isinstance(res.commit_sha, dict)
    assert set(res.commit_sha.keys()) == {"frontend", "backend"}
