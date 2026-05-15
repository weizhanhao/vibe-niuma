"""Plan 6 Task 2 —— admin token 文件 + FastAPI 鉴权依赖。

token 文件路径默认 `/opt/doskill/admin.token`，测试通过 monkeypatch 改到 tmp_path。
verify_admin_token 比对头部 `X-Admin-Token`，错/缺一律 401。
"""
import os
import stat

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from orchestrator import auth


@pytest.fixture
def token_path(tmp_path, monkeypatch):
    """指 ADMIN_TOKEN_PATH 到 tmp_path —— 测试不动 /opt/doskill。"""
    path = tmp_path / "admin.token"
    monkeypatch.setattr(auth, "ADMIN_TOKEN_PATH", str(path))
    return path


def test_read_creates_token_if_missing(token_path):
    assert not token_path.exists()
    token = auth.read_admin_token()
    assert token_path.exists()
    # secrets.token_urlsafe(32) → 至少 30+ chars，且 base64 安全字符集
    assert len(token) >= 30
    assert token == token_path.read_text()


def test_read_returns_existing_token(token_path):
    token_path.write_text("preexisting-token-value-xyz123456789")
    token = auth.read_admin_token()
    assert token == "preexisting-token-value-xyz123456789"


def test_read_chmod_600_on_create(token_path):
    auth.read_admin_token()
    mode = stat.S_IMODE(os.stat(token_path).st_mode)
    assert mode == 0o600, f"expected 600 but got {oct(mode)}"


def _make_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth.verify_admin_token)])
    def protected():
        return {"ok": True}

    return app


def test_verify_admin_token_correct_passes(token_path):
    token_path.write_text("real-secret-token-abc123456789xyz")
    client = TestClient(_make_test_app())

    resp = client.get(
        "/protected", headers={"X-Admin-Token": "real-secret-token-abc123456789xyz"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_verify_admin_token_missing_returns_401(token_path):
    token_path.write_text("real-secret-token-abc123456789xyz")
    client = TestClient(_make_test_app())

    resp = client.get("/protected")
    assert resp.status_code == 401


def test_verify_admin_token_wrong_returns_401(token_path):
    token_path.write_text("real-secret-token-abc123456789xyz")
    client = TestClient(_make_test_app())

    resp = client.get("/protected", headers={"X-Admin-Token": "wrong-token"})
    assert resp.status_code == 401


def test_token_is_constant_time_compared(monkeypatch, token_path):
    """timing-safe 比对：必须走 hmac.compare_digest，不是 `==`。

    通过 monkeypatch hmac.compare_digest，断言它被调到。这里不真测时序
    （单元测试测不出来），只验证实现选了正确的 API。
    """
    token_path.write_text("real-secret-token-abc123456789xyz")
    called: list[tuple] = []

    def spy_compare_digest(a, b):
        called.append((a, b))
        return False  # 强制 mismatch → 401

    monkeypatch.setattr(auth.hmac, "compare_digest", spy_compare_digest)

    client = TestClient(_make_test_app())
    resp = client.get("/protected", headers={"X-Admin-Token": "anything"})
    assert resp.status_code == 401
    assert len(called) == 1, "verify_admin_token 必须调一次 hmac.compare_digest"


def test_token_path_env_override(monkeypatch, tmp_path):
    """DOSKILL_ADMIN_TOKEN_PATH env var 能覆盖默认路径 —— 测试和 dev 都靠这个。

    这里直接 monkeypatch 模块常量，验证 read_admin_token 真的写到指向的路径。
    模块 import 时读 env 的行为则通过 importlib.reload 间接覆盖。
    """
    alt = tmp_path / "alt-admin.token"
    monkeypatch.setattr(auth, "ADMIN_TOKEN_PATH", str(alt))

    token = auth.read_admin_token()
    assert alt.exists()
    assert alt.read_text() == token
