"""POST /projects/{project_id}/sync-repos 集成测试 —— 通过 TestClient 全链路打。

不依赖 DB（端点本身不碰 DB）。用本地 bare repo 模拟 GitHub。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _seed_bare(path: Path, *, branch: str = "main") -> Path:
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient + 把 settings.workspaces_root 指到 tmp_path + 放宽 URL 校验。"""
    # 让 parse_github_url 接受 file:// URL
    from orchestrator import github_client, multi_repo_sync

    def fake_parse(url: str) -> tuple[str, str]:
        return ("local", Path(url.replace("file://", "")).stem)

    monkeypatch.setattr(github_client, "parse_github_url", fake_parse)
    monkeypatch.setattr(multi_repo_sync, "parse_github_url", fake_parse)

    # 把 workspaces_root 指到 tmp
    from orchestrator.config import settings
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))

    from orchestrator.main import app
    return TestClient(app)


def test_sync_repos_endpoint_happy_path(client, tmp_path):
    remote = _seed_bare(tmp_path / "frontend.git", branch="main")

    resp = client.post(
        "/projects/proj-x1/sync-repos",
        json={
            "repos": [
                {"url": f"file://{remote}", "main_branch": "main", "target_branch": "vibe-niuma/dev"},
            ],
            "pat": None,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_id"] == "proj-x1"
    assert len(body["synced"]) == 1
    assert len(body["failed"]) == 0
    synced = body["synced"][0]
    assert synced["name"] == "frontend"
    assert synced["target_branch_created"] is True
    assert synced["target_branch"] == "vibe-niuma/dev"


def test_sync_repos_endpoint_empty_list(client):
    resp = client.post(
        "/projects/proj-empty/sync-repos",
        json={"repos": [], "pat": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["synced"] == []
    assert body["failed"] == []


def test_sync_repos_endpoint_mixed_good_and_bad(client, tmp_path):
    good = _seed_bare(tmp_path / "good.git", branch="main")

    resp = client.post(
        "/projects/proj-mix/sync-repos",
        json={
            "repos": [
                {"url": f"file://{good}", "main_branch": "main", "target_branch": "vibe-niuma/dev"},
                {"url": f"file://{tmp_path}/missing.git", "main_branch": "main", "target_branch": "vibe-niuma/dev"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["synced"]) == 1
    assert len(body["failed"]) == 1
    assert body["synced"][0]["name"] == "good"
    assert body["failed"][0]["url"].endswith("missing.git")


def test_sync_repos_endpoint_rejects_unsafe_project_id(client):
    """project_id 不能含 / 或 ..（防 path traversal）。"""
    resp = client.post(
        "/projects/..%2F..%2Fetc/sync-repos",
        json={"repos": [], "pat": None},
    )
    # URL decoder 后是 ../../etc → 安全检查应该拦
    assert resp.status_code in (400, 404)


def test_sync_repos_endpoint_idempotent(client, tmp_path):
    remote = _seed_bare(tmp_path / "rep.git", branch="main")
    payload = {
        "repos": [
            {"url": f"file://{remote}", "main_branch": "main", "target_branch": "vibe-niuma/dev"},
        ],
    }
    # 第一次
    r1 = client.post("/projects/proj-idem/sync-repos", json=payload).json()
    assert r1["synced"][0]["target_branch_created"] is True
    # 第二次：work_dir 已存在 + target_branch 已存在 → 不重创
    r2 = client.post("/projects/proj-idem/sync-repos", json=payload).json()
    assert r2["synced"][0]["target_branch_created"] is False
    assert r2["synced"][0]["work_dir"] == r1["synced"][0]["work_dir"]
