"""POST /admin/alert 端点测试 —— mock 业务员 webhook，验证转发链路。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_token(tmp_path, monkeypatch) -> str:
    token = "test-alert-admin-token-xxxx"
    tok_file = tmp_path / "admin.token"
    tok_file.write_text(token)
    from orchestrator import auth
    monkeypatch.setattr(auth, "ADMIN_TOKEN_PATH", str(tok_file))
    return token


@pytest.fixture
def client_with_alert_mock(monkeypatch, admin_token):
    sent: list = []

    class FakeClient:
        async def send(self, msg):
            sent.append({"title": msg.title, "body": msg.body, "link_url": msg.link_url})

    from orchestrator import alert
    monkeypatch.setattr(alert, "detect_client", lambda url, **kw: FakeClient())

    from orchestrator.main import app
    return TestClient(app), sent


def test_alert_happy_path(client_with_alert_mock, admin_token):
    client, sent = client_with_alert_mock
    resp = client.post(
        "/admin/alert",
        headers={"X-Admin-Token": admin_token},
        json={
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=fake",
            "title": "vibe-niuma 异常",
            "body": "mysql 挂了，业务员合并失败",
            "link_url": "http://x/cr/123",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert len(sent) == 1
    assert sent[0]["title"] == "vibe-niuma 异常"
    assert "mysql 挂了" in sent[0]["body"]
    assert sent[0]["link_url"] == "http://x/cr/123"


def test_alert_requires_admin_token(client_with_alert_mock, admin_token):
    client, _ = client_with_alert_mock
    resp = client.post(
        "/admin/alert",
        json={"webhook_url": "https://oapi.dingtalk.com/x", "title": "t", "body": "b"},
    )
    assert resp.status_code == 401


def test_alert_rejects_unsupported_webhook_url(monkeypatch, admin_token):
    from orchestrator.main import app
    client = TestClient(app)
    resp = client.post(
        "/admin/alert",
        headers={"X-Admin-Token": admin_token},
        json={
            "webhook_url": "https://example.com/random",
            "title": "t", "body": "b",
        },
    )
    assert resp.status_code == 422
    assert "不支持" in resp.json()["detail"]


def test_alert_502_when_webhook_send_fails(monkeypatch, admin_token):
    from orchestrator import alert as alert_mod

    class FailingClient:
        async def send(self, msg):
            raise alert_mod.AlertError("dingtalk errcode=310000: keywords not in content")

    monkeypatch.setattr(alert_mod, "detect_client", lambda url, **kw: FailingClient())

    from orchestrator.main import app
    client = TestClient(app)
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


def test_alert_422_when_empty_title(client_with_alert_mock, admin_token):
    client, _ = client_with_alert_mock
    resp = client.post(
        "/admin/alert",
        headers={"X-Admin-Token": admin_token},
        json={"webhook_url": "https://oapi.dingtalk.com/x", "title": "", "body": "b"},
    )
    assert resp.status_code == 422
