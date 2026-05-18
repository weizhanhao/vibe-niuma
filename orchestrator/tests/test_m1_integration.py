"""M1 端到端集成测试 —— 把 T3-T7 全部串起来跑一遍：

模拟业务员配 1 个 GitHub 仓 → sync-repos clone + 创建 vibe-niuma/dev →
模拟 CR：从 dev 切 cr 分支 + 改文件 + commit → merge_to_target push 到
remote → maintain_pr 维护 PR。

验证：
- bare remote 上 vibe-niuma/dev 有 cr 的 commit
- GitHubAPI 收到 POST /pulls（创建 PR）
- PR body 含 cr_id + 业务员原话
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from orchestrator.auto_pr import CRRecord, maintain_pr
from orchestrator.git_manager import GitManager
from orchestrator.github_client import GitHubAPI
from orchestrator.multi_repo_sync import RepoSpec, sync_one


# ── fakes ──────────────────────────────────────────────────────────


def _seed_bare(path: Path) -> Path:
    """搭一个 bare repo + 推一个 seed commit 上 main。"""
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
    if current != "main":
        subprocess.run(["git", "branch", "-M", "main"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=work, check=True, capture_output=True)
    return path


@pytest.fixture(autouse=True)
def _patch_parse_url(monkeypatch):
    """multi_repo_sync 要 GitHub URL，测试用 file://，跳过 URL 校验。"""
    from orchestrator import github_client, multi_repo_sync

    def fake_parse(url: str) -> tuple[str, str]:
        return ("local", Path(url.replace("file://", "")).stem)

    monkeypatch.setattr(github_client, "parse_github_url", fake_parse)
    monkeypatch.setattr(multi_repo_sync, "parse_github_url", fake_parse)


@pytest.fixture
def patch_async_client(monkeypatch):
    holder: dict = {"transport": None}
    real_init = httpx.AsyncClient.__init__

    def _init(self, *a, **kw):
        if holder["transport"] is not None:
            kw["transport"] = holder["transport"]
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return holder


# ── 端到端 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_m1_full_chain_sync_cr_merge_push_pr(tmp_path, patch_async_client):
    """业务员配 1 仓 → sync → 起 CR → merge 进 dev push → 维护 PR。"""
    # ── 1. 准备 bare remote 当 fake GitHub ──
    remote = _seed_bare(tmp_path / "myrepo.git")
    workspaces = tmp_path / "workspaces"
    project_id = "proj-integration"

    # ── 2. sync_one：clone + 创建 vibe-niuma/dev + push ──
    spec = RepoSpec(
        url=f"file://{remote}",
        main_branch="main",
        target_branch="vibe-niuma/dev",
    )
    sync_result = await sync_one(spec, project_id=project_id, pat=None, workspaces_root=workspaces)
    assert sync_result.target_branch_created is True
    work_dir = Path(sync_result.work_dir)
    assert (work_dir / "README.md").exists()

    # 验证：remote 上现在有 vibe-niuma/dev
    branches_on_remote = subprocess.run(
        ["git", "branch", "-a"], cwd=remote, capture_output=True, text=True,
    ).stdout
    assert "vibe-niuma/dev" in branches_on_remote

    # ── 3. 模拟一条 CR：从 dev 切 cr/integration-1 → 改文件 → commit ──
    gm = GitManager(str(work_dir))
    gm.create_branch("cr/integration-1", base_branch="vibe-niuma/dev")
    (work_dir / "business_change.txt").write_text("业务员说：把订单徽章改红色\n")
    commit_sha = gm.commit_all("cr/integration-1", "cr: 业务员改了徽章颜色")

    # ── 4. merge_to_target 合到 vibe-niuma/dev + push 回 origin ──
    gm.merge_to_target(
        "cr/integration-1",
        target="vibe-niuma/dev",
        push_remote=True,
        push_pat=None,  # bare file:// 不需要 pat
    )

    # 验证：bare remote 的 vibe-niuma/dev 上有 business_change.txt
    files_on_remote = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "vibe-niuma/dev"],
        cwd=remote, capture_output=True, text=True,
    ).stdout
    assert "business_change.txt" in files_on_remote

    # ── 5. auto_pr.maintain_pr：fake GitHub API ──
    api_calls: list[dict] = []

    def gh_handler(request: httpx.Request) -> httpx.Response:
        api_calls.append({
            "method": request.method,
            "url": str(request.url),
            "body": request.content.decode() if request.content else "",
        })
        # 第一次 GET /pulls 返回空（没有现存 PR）→ upsert 走 create
        if request.method == "GET" and "/pulls" in str(request.url):
            return httpx.Response(200, json=[])
        # POST /pulls → create
        if request.method == "POST" and "/pulls" in str(request.url):
            return httpx.Response(201, json={
                "number": 1,
                "html_url": "https://github.com/local/myrepo/pull/1",
            })
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    patch_async_client["transport"] = httpx.MockTransport(gh_handler)
    api = GitHubAPI(pat="ghp_fake")

    record = CRRecord(
        cr_id="cr-integration-1",
        business_text="把订单徽章改红色",
        commit_sha=commit_sha,
        timestamp_iso="2026-05-18T16:30:00",
    )
    pr, created = await maintain_pr(
        api,
        owner="local",
        repo="myrepo",
        target_branch="vibe-niuma/dev",
        base_branch="main",
        records=[record],
    )

    assert created is True
    assert pr["number"] == 1

    # ── 6. 全链路 assertions ──
    # 6a. GitHub API 被打了 GET + POST 各一次
    assert len(api_calls) == 2
    assert api_calls[0]["method"] == "GET"
    assert api_calls[1]["method"] == "POST"

    # 6b. POST 的 body 含正确 head/base 分支 + 业务员原话
    post_body = json.loads(api_calls[1]["body"])
    assert post_body["head"] == "vibe-niuma/dev"
    assert post_body["base"] == "main"
    assert "cr-integration-1" in post_body["body"]
    assert "把订单徽章改红色" in post_body["body"]
    assert commit_sha[:10] in post_body["body"]
    assert "[vibe-niuma]" in post_body["title"]


@pytest.mark.asyncio
async def test_m1_second_cr_updates_existing_pr(tmp_path, patch_async_client):
    """业务员合第 2 条 CR：upsert_pr 应该 PATCH 已存在的 PR（不 POST 新）。"""
    remote = _seed_bare(tmp_path / "myrepo2.git")
    workspaces = tmp_path / "workspaces"

    spec = RepoSpec(url=f"file://{remote}", main_branch="main", target_branch="vibe-niuma/dev")
    await sync_one(spec, project_id="proj-2", pat=None, workspaces_root=workspaces)
    work_dir = workspaces / "proj-2" / "myrepo2"

    gm = GitManager(str(work_dir))

    # 第 1 条 CR
    gm.create_branch("cr/one", base_branch="vibe-niuma/dev")
    (work_dir / "one.txt").write_text("a\n")
    sha1 = gm.commit_all("cr/one", "cr 1")
    gm.merge_to_target("cr/one", target="vibe-niuma/dev", push_remote=True)

    # 第 2 条 CR
    gm.create_branch("cr/two", base_branch="vibe-niuma/dev")
    (work_dir / "two.txt").write_text("b\n")
    sha2 = gm.commit_all("cr/two", "cr 2")
    gm.merge_to_target("cr/two", target="vibe-niuma/dev", push_remote=True)

    # PR 状态：第 1 条已合时已经创建过 PR；第 2 条应该 update
    api_calls: list[dict] = []
    existing_pr_returned = {"flag": False}

    def gh_handler(request: httpx.Request) -> httpx.Response:
        api_calls.append({"method": request.method, "url": str(request.url)})
        if request.method == "GET" and "/pulls" in str(request.url):
            if not existing_pr_returned["flag"]:
                # 第一次：没有现存 → upsert 走 create
                return httpx.Response(200, json=[])
            # 之后：返回已存在
            return httpx.Response(200, json=[{"number": 7}])
        if request.method == "POST" and "/pulls" in str(request.url):
            existing_pr_returned["flag"] = True
            return httpx.Response(201, json={"number": 7})
        if request.method == "PATCH" and "/pulls/7" in str(request.url):
            return httpx.Response(200, json={"number": 7})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    patch_async_client["transport"] = httpx.MockTransport(gh_handler)
    api = GitHubAPI(pat="ghp_fake")

    # 模拟业务员合第 1 条之后即时维护 PR
    r1 = CRRecord(cr_id="cr-one", business_text="改 1", commit_sha=sha1, timestamp_iso="2026-05-18T10:00:00")
    pr1, created1 = await maintain_pr(api, owner="local", repo="myrepo2",
                                       target_branch="vibe-niuma/dev", base_branch="main", records=[r1])
    assert created1 is True

    # 第 2 条之后：维护 PR 含两条
    r2 = CRRecord(cr_id="cr-two", business_text="改 2", commit_sha=sha2, timestamp_iso="2026-05-18T11:00:00")
    pr2, created2 = await maintain_pr(api, owner="local", repo="myrepo2",
                                       target_branch="vibe-niuma/dev", base_branch="main", records=[r1, r2])
    assert created2 is False  # PATCH 更新，不创建新 PR
    assert pr2["number"] == 7

    # 验证调用序：GET, POST（创第 1 个 PR）, GET, PATCH（更新成 2 条）
    methods = [c["method"] for c in api_calls]
    assert methods == ["GET", "POST", "GET", "PATCH"]
