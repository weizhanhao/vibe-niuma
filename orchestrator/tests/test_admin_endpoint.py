"""Plan 6 Task 3 —— /admin/config GET + PUT 端点 + settings 热重载。

测试在独立的 FastAPI app 上跑（用 admin_router 直接 mount），避免拉起完整的
orchestrator app（不依赖 lifespan / git repo 等）。token 文件 monkeypatch 到 tmp。
DB 用 conftest 的 db_session（MySQL 测试库），通过 dependency_overrides 注入。
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from orchestrator import auth, config as config_mod
from orchestrator.admin import router as admin_router
from orchestrator.db import get_db
from orchestrator.models import SystemConfig


TOKEN_VALUE = "test-admin-token-abc123456789"


@pytest.fixture
def token_path(tmp_path, monkeypatch):
    path = tmp_path / "admin.token"
    path.write_text(TOKEN_VALUE)
    monkeypatch.setattr(auth, "ADMIN_TOKEN_PATH", str(path))
    return path


@pytest.fixture
def clean_config(db_session):
    """每个用例都从空 system_config 表开始。"""
    db_session.query(SystemConfig).delete()
    db_session.commit()
    return db_session


@pytest.fixture
def admin_app(token_path, clean_config) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)

    def _override_get_db() -> Iterator[Session]:
        yield clean_config

    app.dependency_overrides[get_db] = _override_get_db
    return app


@pytest.fixture
def admin_client(admin_app, monkeypatch) -> TestClient:
    # 每个用例都清一遍 settings 缓存，避免跨用例残留
    config_mod.get_settings.cache_clear()
    return TestClient(admin_app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-Admin-Token": TOKEN_VALUE}


# ──────────────────────── GET /admin/config ────────────────────────


def test_get_requires_token(admin_client):
    resp = admin_client.get("/admin/config")
    assert resp.status_code == 401


def test_get_with_wrong_token_returns_401(admin_client):
    resp = admin_client.get("/admin/config", headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401


def test_get_returns_defaults_on_first_call(admin_client, auth_headers):
    """空表 → repo.get_or_create() 建默认行 → 返回 schema 字段全齐。"""
    resp = admin_client.get("/admin/config", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 0
    cfg = body["config"]
    assert cfg["dev_runner"] == "opencode"
    assert cfg["dev_model"] == "deepseek/deepseek-v4-flash"
    assert cfg["vision_model"] == "qwen-vl-plus"
    assert cfg["demo_repo_path"] == "/opt/doskill/demo"
    assert cfg["preview_backend_url"] == "http://doskill-demo-backend:8000"
    # 密钥字段空 → 值 null + set flag false
    assert cfg["deepseek_api_key"] is None
    assert cfg["deepseek_api_key_set"] is False
    assert cfg["dashscope_api_key"] is None
    assert cfg["dashscope_api_key_set"] is False
    assert cfg["anthropic_api_key"] is None
    assert cfg["anthropic_api_key_set"] is False


def test_get_returns_config_with_version_and_set_flags(
    admin_client, auth_headers, clean_config
):
    """提交一行带 deepseek key 的 SystemConfig，GET 返回密钥脱敏 + set=true。"""
    cfg = SystemConfig(id=1, deepseek_api_key="sk-secret-xyz", version=3)
    clean_config.add(cfg)
    clean_config.commit()

    resp = admin_client.get("/admin/config", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 3
    # 永远不回显密钥本体
    assert body["config"]["deepseek_api_key"] is None
    # 但 set flag 告诉 UI「已设置」
    assert body["config"]["deepseek_api_key_set"] is True
    assert body["config"]["dashscope_api_key_set"] is False
    assert body["config"]["anthropic_api_key_set"] is False


# ──────────────────────── PUT /admin/config ────────────────────────


def test_put_requires_token(admin_client):
    resp = admin_client.put(
        "/admin/config",
        json={"config": {"dev_model": "x"}, "expectedVersion": 0},
    )
    assert resp.status_code == 401


def test_put_with_correct_expected_version_succeeds_and_bumps(
    admin_client, auth_headers
):
    # 先 GET 取当前 version
    initial = admin_client.get("/admin/config", headers=auth_headers).json()
    assert initial["version"] == 0

    resp = admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={
            "config": {"dev_model": "claude-sonnet-4"},
            "expectedVersion": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["config"]["dev_model"] == "claude-sonnet-4"
    # restartedServices 字段总是存在（即使空 list）
    assert "restartedServices" in body
    assert isinstance(body["restartedServices"], list)


def test_put_with_stale_version_returns_409(admin_client, auth_headers):
    admin_client.get("/admin/config", headers=auth_headers)  # 建默认行 → version=0
    # 第一次 PUT → version=1
    admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={"config": {"dev_model": "x"}, "expectedVersion": 0},
    )

    # 第二次还拿 0 当 expected → 409
    resp = admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={"config": {"dev_model": "y"}, "expectedVersion": 0},
    )
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"].lower()


def test_put_with_invalid_dev_runner_value_returns_422(admin_client, auth_headers):
    """dev_runner 必须是 'opencode' 或 'claude-code' —— 其它值 Pydantic 拒。"""
    admin_client.get("/admin/config", headers=auth_headers)
    resp = admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={
            "config": {"dev_runner": "invalid-runner-name"},
            "expectedVersion": 0,
        },
    )
    assert resp.status_code == 422


def test_put_triggers_litellm_restart_when_provider_key_changed(
    admin_client, auth_headers, monkeypatch
):
    """任一 *_api_key 字段变了 → subprocess.run 调 systemctl restart doskill-llm-proxy。"""
    from orchestrator import admin as admin_mod

    calls: list[tuple] = []

    def fake_run(argv, *args, **kwargs):
        calls.append((tuple(argv), kwargs))

        class _Result:
            returncode = 0
        return _Result()

    monkeypatch.setattr(admin_mod.subprocess, "run", fake_run)

    admin_client.get("/admin/config", headers=auth_headers)
    resp = admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={
            "config": {"deepseek_api_key": "sk-newvalue"},
            "expectedVersion": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "doskill-llm-proxy" in body["restartedServices"]
    # 密钥脱敏依然生效
    assert body["config"]["deepseek_api_key"] is None
    assert body["config"]["deepseek_api_key_set"] is True

    # subprocess.run 被调一次，args 含 systemctl restart doskill-llm-proxy
    assert len(calls) == 1
    argv = calls[0][0]
    assert "systemctl" in argv
    assert "restart" in argv
    assert "doskill-llm-proxy" in argv


def test_put_changing_model_does_not_restart(
    admin_client, auth_headers, monkeypatch
):
    """改 dev_model 不需要重启 LiteLLM —— 仅 settings cache invalidate。"""
    from orchestrator import admin as admin_mod

    calls: list[tuple] = []

    def fake_run(argv, *args, **kwargs):
        calls.append((tuple(argv), kwargs))

        class _Result:
            returncode = 0
        return _Result()

    monkeypatch.setattr(admin_mod.subprocess, "run", fake_run)

    admin_client.get("/admin/config", headers=auth_headers)

    # 监视 cache_clear 是否被调
    clear_calls: list[bool] = []
    orig_clear = config_mod.get_settings.cache_clear

    def spy_clear():
        clear_calls.append(True)
        return orig_clear()

    monkeypatch.setattr(
        config_mod.get_settings, "cache_clear", spy_clear
    )

    resp = admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={"config": {"dev_model": "deepseek-chat-v2"}, "expectedVersion": 0},
    )
    assert resp.status_code == 200
    assert resp.json()["restartedServices"] == []
    assert calls == [], "改 model 不该触发 systemctl restart"
    assert len(clear_calls) >= 1, "改 model 必须 invalidate settings cache"


def test_put_partial_preserves_other_fields(admin_client, auth_headers):
    """PUT 只带一个字段 → 其它字段保持原值。"""
    admin_client.get("/admin/config", headers=auth_headers)
    resp = admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={"config": {"vision_model": "qwen-vl-max"}, "expectedVersion": 0},
    )
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert cfg["vision_model"] == "qwen-vl-max"
    # 其它字段不变
    assert cfg["dev_runner"] == "opencode"
    assert cfg["dev_model"] == "deepseek/deepseek-v4-flash"
    assert cfg["demo_repo_path"] == "/opt/doskill/demo"


def test_get_after_put_reflects_new_values(admin_client, auth_headers):
    admin_client.get("/admin/config", headers=auth_headers)
    admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={
            "config": {
                "dev_model": "claude-opus-4",
                "preview_backend_url": "http://new-backend:9000",
            },
            "expectedVersion": 0,
        },
    )

    resp = admin_client.get("/admin/config", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["config"]["dev_model"] == "claude-opus-4"
    assert body["config"]["preview_backend_url"] == "http://new-backend:9000"


def test_put_with_only_api_key_returns_set_true_in_response(
    admin_client, auth_headers, monkeypatch
):
    """PUT 设新 anthropic_api_key → response config.anthropic_api_key is null,
    anthropic_api_key_set is true."""
    from orchestrator import admin as admin_mod

    def fake_run(argv, *args, **kwargs):
        class _Result:
            returncode = 0
        return _Result()

    monkeypatch.setattr(admin_mod.subprocess, "run", fake_run)

    admin_client.get("/admin/config", headers=auth_headers)
    resp = admin_client.put(
        "/admin/config",
        headers=auth_headers,
        json={
            "config": {"anthropic_api_key": "sk-ant-xyz"},
            "expectedVersion": 0,
        },
    )
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert cfg["anthropic_api_key"] is None
    assert cfg["anthropic_api_key_set"] is True
