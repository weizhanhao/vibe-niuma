"""github_client 单元测试。

三块：
1. URL 解析 + git CLI 工具的纯函数测试
2. git clone / fetch / push / remote_branch_exists 用本地 bare repo 当远端跑
3. GitHubAPI 用 httpx.MockTransport 不打真 github.com
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from orchestrator.github_client import (
    AuthError,
    GitHubAPI,
    GitOperationError,
    NotFoundError,
    RateLimitError,
    _git_with_pat,
    clone,
    fetch,
    parse_github_url,
    push,
    remote_branch_exists,
)


# ── parse_github_url ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/weizhanhao/vibe-niuma.git", ("weizhanhao", "vibe-niuma")),
        ("https://github.com/weizhanhao/vibe-niuma", ("weizhanhao", "vibe-niuma")),
        ("https://github.com/weizhanhao/vibe-niuma/", ("weizhanhao", "vibe-niuma")),
        ("git@github.com:weizhanhao/vibe-niuma.git", ("weizhanhao", "vibe-niuma")),
        ("git@github.com:weizhanhao/vibe-niuma", ("weizhanhao", "vibe-niuma")),
    ],
)
def test_parse_github_url_supports_both_https_and_ssh(url, expected):
    assert parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/foo/bar.git",
        "https://github.com/onlyowner",
        "https://github.com/o/r/extra",
        "",
        "not-a-url",
    ],
)
def test_parse_github_url_rejects_non_github(url):
    with pytest.raises(ValueError, match="不像 GitHub URL"):
        parse_github_url(url)


# ── _git_with_pat ──────────────────────────────────────────────────


def test_git_with_pat_no_token_returns_bare_git():
    assert _git_with_pat(None) == ["git"]
    assert _git_with_pat("") == ["git"]


def test_git_with_pat_injects_authorization_header():
    cmd = _git_with_pat("ghp_xxxxx")
    assert cmd[0] == "git"
    assert cmd[1] == "-c"
    assert cmd[2] == "http.extraHeader=Authorization: bearer ghp_xxxxx"


# ── git CLI wrappers（用本地 bare repo 当 fake remote） ────────────


def _init_bare_repo(path: Path) -> Path:
    """造一个空的 bare repo + 塞一个 commit 进去作为可 clone 的远端。"""
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    # 在临时 work dir 里 push 一个 initial commit 进去
    work = path.parent / f"_seed_{path.name}"
    subprocess.run(["git", "clone", str(path), str(work)], check=True, capture_output=True)
    (work / "README.md").write_text("seed\n")
    subprocess.run(["git", "config", "user.email", "test@vibe-niuma.local"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "vibe-niuma-test"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=work, check=True, capture_output=True)
    # 检测默认分支名（git 2.28+ 可能是 main，老版本是 master）
    head = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "push", "origin", head], cwd=work, check=True, capture_output=True)
    return path


def test_clone_creates_working_repo(tmp_path):
    remote = _init_bare_repo(tmp_path / "remote.git")
    target = tmp_path / "local"
    clone(f"file://{remote}", target)
    assert (target / ".git").is_dir()
    assert (target / "README.md").exists()


def test_clone_bare_does_not_create_working_tree(tmp_path):
    remote = _init_bare_repo(tmp_path / "remote.git")
    target = tmp_path / "cache.git"
    clone(f"file://{remote}", target, bare=True)
    assert (target / "HEAD").exists()  # bare 仓有 HEAD 在根目录
    assert not (target / ".git").exists()  # bare 没有 .git 子目录


def test_clone_refuses_when_target_exists(tmp_path):
    target = tmp_path / "exists"
    target.mkdir()
    with pytest.raises(ValueError, match="target_dir 已存在"):
        clone("file:///nonexistent.git", target)


def test_clone_raises_on_invalid_url(tmp_path):
    target = tmp_path / "fail"
    with pytest.raises(GitOperationError):
        clone("file:///definitely-not-here.git", target)


def test_fetch_runs_without_error(tmp_path):
    remote = _init_bare_repo(tmp_path / "remote.git")
    local = tmp_path / "local"
    clone(f"file://{remote}", local)
    fetch(local)  # 不抛即通过


def test_push_propagates_branch(tmp_path):
    remote = _init_bare_repo(tmp_path / "remote.git")
    local = tmp_path / "local"
    clone(f"file://{remote}", local)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=local, check=True, capture_output=True)
    (local / "f.txt").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "f"], cwd=local, check=True, capture_output=True)
    push(local, "feature", set_upstream=True)
    # 验证远端真有 feature 分支
    assert remote_branch_exists(local, "feature") is True


def test_remote_branch_exists_returns_false_for_missing(tmp_path):
    remote = _init_bare_repo(tmp_path / "remote.git")
    local = tmp_path / "local"
    clone(f"file://{remote}", local)
    assert remote_branch_exists(local, "does-not-exist") is False


# ── GitHubAPI（mock httpx） ────────────────────────────────────────


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def patch_async_client(monkeypatch):
    """把 httpx.AsyncClient 的 transport 替成 callable。"""
    holder: dict = {"transport": None}
    real_init = httpx.AsyncClient.__init__

    def _init(self, *a, **kw):
        if holder["transport"] is not None:
            kw["transport"] = holder["transport"]
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return holder


@pytest.mark.asyncio
async def test_get_user_sends_bearer_auth(patch_async_client):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={"login": "weizhanhao", "id": 1})

    patch_async_client["transport"] = _mock_transport(handler)
    api = GitHubAPI(pat="ghp_xxxxx")
    user = await api.get_user()
    assert user["login"] == "weizhanhao"
    assert captured["url"] == "https://api.github.com/user"
    assert captured["auth"] == "Bearer ghp_xxxxx"
    assert "application/vnd.github" in captured["accept"]


@pytest.mark.asyncio
async def test_get_user_401_raises_auth_error(patch_async_client):
    patch_async_client["transport"] = _mock_transport(
        lambda req: httpx.Response(401, json={"message": "Bad credentials"})
    )
    api = GitHubAPI(pat="bad")
    with pytest.raises(AuthError, match="PAT 无效"):
        await api.get_user()


@pytest.mark.asyncio
async def test_403_with_zero_rate_limit_raises_rate_limit(patch_async_client):
    patch_async_client["transport"] = _mock_transport(
        lambda req: httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"},
        )
    )
    api = GitHubAPI(pat="x")
    with pytest.raises(RateLimitError, match="限流"):
        await api.get_user()


@pytest.mark.asyncio
async def test_403_without_rate_limit_raises_auth_error(patch_async_client):
    patch_async_client["transport"] = _mock_transport(
        lambda req: httpx.Response(403, json={"message": "Forbidden"})
    )
    api = GitHubAPI(pat="x")
    with pytest.raises(AuthError, match="权限不足"):
        await api.get_user()


@pytest.mark.asyncio
async def test_404_raises_not_found(patch_async_client):
    patch_async_client["transport"] = _mock_transport(
        lambda req: httpx.Response(404, json={"message": "Not Found"})
    )
    api = GitHubAPI(pat="x")
    with pytest.raises(NotFoundError):
        await api.get_user()


@pytest.mark.asyncio
async def test_find_pr_returns_first_match(patch_async_client):
    def handler(request: httpx.Request) -> httpx.Response:
        # 校验 query string 形状
        assert "head=weizhanhao:vibe-niuma/dev" in str(request.url)
        assert "base=main" in str(request.url)
        return httpx.Response(200, json=[{"number": 42, "title": "WIP"}])

    patch_async_client["transport"] = _mock_transport(handler)
    api = GitHubAPI(pat="x")
    pr = await api.find_pr("weizhanhao", "repo", head_branch="vibe-niuma/dev", base_branch="main")
    assert pr is not None
    assert pr["number"] == 42


@pytest.mark.asyncio
async def test_find_pr_returns_none_when_empty(patch_async_client):
    patch_async_client["transport"] = _mock_transport(lambda req: httpx.Response(200, json=[]))
    api = GitHubAPI(pat="x")
    assert await api.find_pr("o", "r", head_branch="dev", base_branch="main") is None


@pytest.mark.asyncio
async def test_create_pr_posts_correct_body(patch_async_client):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/o/r/pull/7"})

    patch_async_client["transport"] = _mock_transport(handler)
    api = GitHubAPI(pat="x")
    pr = await api.create_pr(
        "o", "r", head_branch="vibe-niuma/dev", base_branch="main",
        title="业务员合并", body="改了徽章颜色",
    )
    assert pr["number"] == 7
    assert captured["method"] == "POST"
    assert captured["body"] == {
        "title": "业务员合并",
        "head": "vibe-niuma/dev",
        "base": "main",
        "body": "改了徽章颜色",
        "draft": False,
    }


@pytest.mark.asyncio
async def test_upsert_pr_creates_when_none_exists(patch_async_client):
    state = {"calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if request.method == "POST":
            return httpx.Response(201, json={"number": 1})
        raise AssertionError(request.method)

    patch_async_client["transport"] = _mock_transport(handler)
    api = GitHubAPI(pat="x")
    pr, created = await api.upsert_pr(
        "o", "r", head_branch="dev", base_branch="main", title="t", body="b"
    )
    assert created is True
    assert pr["number"] == 1
    assert state["calls"] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_upsert_pr_updates_when_exists(patch_async_client):
    state = {"calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[{"number": 99}])
        if request.method == "PATCH":
            return httpx.Response(200, json={"number": 99, "body": "updated"})
        raise AssertionError(request.method)

    patch_async_client["transport"] = _mock_transport(handler)
    api = GitHubAPI(pat="x")
    pr, created = await api.upsert_pr(
        "o", "r", head_branch="dev", base_branch="main", title="t", body="b"
    )
    assert created is False
    assert pr["number"] == 99
    assert state["calls"] == ["GET", "PATCH"]


def test_github_api_rejects_empty_pat():
    with pytest.raises(ValueError, match="PAT 不能为空"):
        GitHubAPI(pat="")
