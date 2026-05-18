"""Plan 11 M3.T24 M3 集成测试 ——

模拟一整条业务员路径：
- mysql 挂 → /health 报 red + services.mysql=down
- 业务员 UI 看到红灯横幅 → 点 ReportToDevButton → POST /admin/alert
- orchestrator detect_client(钉钉 URL) → 给钉钉发上下文带 last_cr/console errors

全 mock，跑得快。验证 /health 状态 + /admin/alert 调用链 + AlertMessage 内容。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_token(tmp_path, monkeypatch) -> str:
    token = "m3-integration-token-abc"
    tok_file = tmp_path / "admin.token"
    tok_file.write_text(token)
    from orchestrator import auth
    monkeypatch.setattr(auth, "ADMIN_TOKEN_PATH", str(tok_file))
    return token


@pytest.fixture
def client(monkeypatch):
    from orchestrator.main import app, app_state
    app_state.session_factory = None
    return TestClient(app)


def test_m3_mysql_down_then_business_user_reports(monkeypatch, client, admin_token):
    """整条 M3 路径：mysql 挂 → /health red → /admin/alert 发钉钉。"""
    from orchestrator.main import app_state
    app_state.session_factory = MagicMock(side_effect=RuntimeError("mysql connection refused"))

    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    health = health_resp.json()
    assert health["status"] == "red"
    assert health["services"]["mysql"] == "down"
    assert health["services"]["orchestrator"] == "ok"

    sent: list = []

    class FakeClient:
        async def send(self, msg):
            sent.append({"title": msg.title, "body": msg.body, "link_url": msg.link_url})

    from orchestrator import alert as alert_mod
    monkeypatch.setattr(alert_mod, "detect_client", lambda url, **kw: FakeClient())

    alert_resp = client.post(
        "/admin/alert",
        headers={"X-Admin-Token": admin_token},
        json={
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=fake",
            "title": "⚠️ vibe-niuma 业务员上报",
            "body": (
                "【业务员留言】\n点保存按钮就报红\n"
                "\n最近失败 CR: cr-12345\n"
                "\n【浏览器 console 错误】\nTypeError: x is undefined\n"
            ),
        },
    )
    assert alert_resp.status_code == 200, alert_resp.text
    assert alert_resp.json() == {"ok": True}

    assert len(sent) == 1
    msg = sent[0]
    assert msg["title"] == "⚠️ vibe-niuma 业务员上报"
    assert "cr-12345" in msg["body"]
    assert "TypeError: x is undefined" in msg["body"]
    assert "点保存按钮就报红" in msg["body"]


def test_m3_health_endpoint_returns_expected_shape(client):
    """无 session/无 url 时，/health 整体结构对（已在 test_health 单测覆盖
    yellow / red 分支；这里只验整条 endpoint 装配）"""
    from orchestrator.main import app_state
    app_state.session_factory = None

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"  # mysql=unknown, llm/main demo url 空 → 全 ok
    assert body["services"]["orchestrator"] == "ok"
    assert body["services"]["mysql"] == "unknown"
    assert body["services"]["llm_proxy"] == "unknown"
    assert body["services"]["main_demo"] == "unknown"
    assert "uptime_seconds" in body
    assert "last_cr_at" in body
    assert "version" in body


def test_m3_alert_unsupported_webhook_url_gives_clear_error(client, admin_token):
    """业务员粘错 URL → 422 + 中文「不支持」（不是 stack）。"""
    resp = client.post(
        "/admin/alert",
        headers={"X-Admin-Token": admin_token},
        json={
            "webhook_url": "https://github.com/api/notifications",
            "title": "t", "body": "b",
        },
    )
    assert resp.status_code == 422
    assert "不支持" in resp.json()["detail"]


def test_m3_alert_propagates_webhook_failure_to_502(monkeypatch, client, admin_token):
    """钉钉 errcode != 0 → 502 + detail 含 errmsg。"""
    from orchestrator import alert as alert_mod

    class FailingClient:
        async def send(self, msg):
            raise alert_mod.AlertError("dingtalk errcode=310000: keywords not in content")

    monkeypatch.setattr(alert_mod, "detect_client", lambda url, **kw: FailingClient())

    resp = client.post(
        "/admin/alert",
        headers={"X-Admin-Token": admin_token},
        json={
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=fake",
            "title": "t", "body": "b",
        },
    )
    assert resp.status_code == 502
    assert "keywords not in content" in resp.json()["detail"]
