"""Plan 8 Task 1-5: multi_repo 子仓发现 + atomic merge happy + rollback + stash 边界。

Test fixtures：用 tmp_path 起真 git 仓库（不 mock subprocess），便于复现 rebase 行为。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

import pytest

from orchestrator.multi_repo import (
    GitConflictError,
    RepoState,
    discover_sub_repos,
    merge_to_main_atomic,
)


# ── helpers ─────────────────────────────────────────────────────────
def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _init_repo(repo: Path, base_files: dict[str, str]) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@vibe-niuma")
    _git(repo, "config", "user.name", "test")
    for name, content in base_files.items():
        (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")


def _add_branch_commit(repo: Path, branch: str, files: dict[str, str]) -> None:
    _git(repo, "checkout", "-q", "-b", branch)
    for name, content in files.items():
        (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{branch} change")


def _add_main_commit(repo: Path, files: dict[str, str]) -> None:
    _git(repo, "checkout", "-q", "main")
    for name, content in files.items():
        (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "main forward")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path / "project"


def _make_sub_repos(project: Path, names: Iterable[str]) -> list[Path]:
    project.mkdir(parents=True, exist_ok=True)
    paths = []
    for n in names:
        repo = project / n
        _init_repo(repo, {"README.md": f"# {n}\n"})
        _add_branch_commit(repo, "cr/x", {"feature.txt": f"{n} feature\n"})
        _git(repo, "checkout", "-q", "main")
        paths.append(repo)
    return paths


# ── discover_sub_repos ─────────────────────────────────────────────
def test_discover_returns_dirs_with_dot_git(project: Path) -> None:
    _make_sub_repos(project, ["frontend", "backend"])
    out = discover_sub_repos(project)
    assert sorted(p.name for p in out) == ["backend", "frontend"]


def test_discover_ignores_top_level_files(project: Path) -> None:
    _make_sub_repos(project, ["a"])
    (project / "AGENTS.md").write_text("plain file")
    out = discover_sub_repos(project)
    assert [p.name for p in out] == ["a"]


def test_discover_returns_sorted_by_name(project: Path) -> None:
    _make_sub_repos(project, ["zebra", "alpha", "mango"])
    out = discover_sub_repos(project)
    assert [p.name for p in out] == ["alpha", "mango", "zebra"]


def test_discover_empty_project_returns_empty_list(project: Path) -> None:
    project.mkdir(parents=True)
    assert discover_sub_repos(project) == []


def test_discover_nonexistent_returns_empty(project: Path) -> None:
    assert discover_sub_repos(project / "does-not-exist") == []


def test_repo_state_is_frozen() -> None:
    s = RepoState(Path("/x"), "cr/y", "abc", "def", False)
    with pytest.raises(AttributeError):
        s.path = Path("/y")  # type: ignore[misc]


# ── merge_to_main_atomic happy ─────────────────────────────────────
@pytest.mark.asyncio
async def test_atomic_merge_2_repos_both_clean_succeeds(project: Path) -> None:
    repos = _make_sub_repos(project, ["frontend", "backend"])
    cr_tips = [_git(r, "rev-parse", "cr/x") for r in repos]
    await merge_to_main_atomic(repos, "cr/x")
    main_tips = [_git(r, "rev-parse", "main") for r in repos]
    assert main_tips == cr_tips


@pytest.mark.asyncio
async def test_atomic_merge_logs_per_repo_progress(project: Path) -> None:
    repos = _make_sub_repos(project, ["frontend", "backend"])
    lines: list[str] = []
    async def sink(line: str) -> None:
        lines.append(line)
    await merge_to_main_atomic(repos, "cr/x", log=sink)
    assert any("[backend]" in ln for ln in lines)
    assert any("[frontend]" in ln for ln in lines)
    assert any("✓ merged" in ln for ln in lines)


@pytest.mark.asyncio
async def test_atomic_merge_drops_stash_after_success(project: Path) -> None:
    repos = _make_sub_repos(project, ["a"])
    (repos[0] / "dirty.txt").write_text("uncommitted local edit\n")
    await merge_to_main_atomic(repos, "cr/x")
    stash_list = _git(repos[0], "stash", "list")
    assert stash_list == ""


# ── Phase 1 conflict + rollback ────────────────────────────────────
@pytest.mark.asyncio
async def test_phase1_conflict_in_second_repo_rolls_back_first(project: Path) -> None:
    repos = _make_sub_repos(project, ["a", "b"])
    _add_main_commit(repos[1], {"feature.txt": "MAIN VERSION CONFLICT\n"})
    a_cr_before = _git(repos[0], "rev-parse", "cr/x")
    a_main_before = _git(repos[0], "rev-parse", "main")
    b_cr_before = _git(repos[1], "rev-parse", "cr/x")
    b_main_before = _git(repos[1], "rev-parse", "main")

    with pytest.raises(GitConflictError) as exc:
        await merge_to_main_atomic(repos, "cr/x")
    assert "b" in str(exc.value)

    assert _git(repos[0], "rev-parse", "cr/x") == a_cr_before
    assert _git(repos[1], "rev-parse", "cr/x") == b_cr_before
    assert _git(repos[0], "rev-parse", "main") == a_main_before
    assert _git(repos[1], "rev-parse", "main") == b_main_before


@pytest.mark.asyncio
async def test_phase1_first_repo_conflict_rolls_back_nothing(project: Path) -> None:
    repos = _make_sub_repos(project, ["only"])
    _add_main_commit(repos[0], {"feature.txt": "MAIN CONFLICT\n"})
    cr_before = _git(repos[0], "rev-parse", "cr/x")
    with pytest.raises(GitConflictError):
        await merge_to_main_atomic(repos, "cr/x")
    assert _git(repos[0], "rev-parse", "cr/x") == cr_before


@pytest.mark.asyncio
async def test_phase1_conflict_message_includes_repo_name(project: Path) -> None:
    repos = _make_sub_repos(project, ["frontend"])
    _add_main_commit(repos[0], {"feature.txt": "X\n"})
    with pytest.raises(GitConflictError) as exc:
        await merge_to_main_atomic(repos, "cr/x")
    assert "frontend" in str(exc.value)


# ── Phase 2 failure + rollback ─────────────────────────────────────
@pytest.mark.asyncio
async def test_phase2_ff_failure_rolls_back_already_merged_main(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = _make_sub_repos(project, ["a", "b"])
    a_main_before = _git(repos[0], "rev-parse", "main")
    a_cr_before = _git(repos[0], "rev-parse", "cr/x")
    b_cr_before = _git(repos[1], "rev-parse", "cr/x")

    import orchestrator.multi_repo as mr
    real = mr._merge_ff_only
    calls = {"n": 0}
    def fake(repo: Path, branch: str) -> tuple[int, str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return real(repo, branch)
        return (1, "simulated phase 2 failure")
    monkeypatch.setattr(mr, "_merge_ff_only", fake)

    with pytest.raises(GitConflictError) as exc:
        await merge_to_main_atomic(repos, "cr/x")
    assert "b" in str(exc.value)

    assert _git(repos[0], "rev-parse", "main") == a_main_before
    assert _git(repos[0], "rev-parse", "cr/x") == a_cr_before
    assert _git(repos[1], "rev-parse", "cr/x") == b_cr_before


# ── stash boundary ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stash_dropped_after_phase1_conflict(project: Path) -> None:
    repos = _make_sub_repos(project, ["a"])
    _add_main_commit(repos[0], {"feature.txt": "X\n"})
    (repos[0] / "dirty.txt").write_text("uncommitted\n")
    with pytest.raises(GitConflictError):
        await merge_to_main_atomic(repos, "cr/x")
    assert _git(repos[0], "stash", "list") == ""


@pytest.mark.asyncio
async def test_no_stash_on_clean_tree_skips_drop(project: Path) -> None:
    repos = _make_sub_repos(project, ["a"])
    await merge_to_main_atomic(repos, "cr/x")
    assert _git(repos[0], "stash", "list") == ""
