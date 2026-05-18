"""POST /admin/provision-ecs 端点集成测试 —— TestClient + 注入 fake 阿里云 client。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from orchestrator.aliyun_provisioner import EcsSpec


@dataclass
class FakeClient:
    """同 test_aliyun_provisioner.FakeClient 的简化版，只够端点测试用。"""
    status_script: list[str] = field(default_factory=lambda: ["Running"])
    create_raises: Optional[Exception] = None
    calls: list[tuple[str, tuple]] = field(default_factory=list)
    next_instance_id: str = "i-test-12345"
    next_public_ip: str = "47.96.88.1"

    def create_instance(self, spec: EcsSpec, region_id: str) -> str:
        self.calls.append(("create_instance", (spec.instance_type, region_id, spec.password)))
        if self.create_raises:
            raise self.create_raises
        return self.next_instance_id

    def describe_instance_status(self, instance_id: str) -> str:
        if self.status_script:
            return self.status_script.pop(0)
        return "Running"

    def allocate_public_ip(self, instance_id: str) -> str:
        self.calls.append(("allocate_public_ip", (instance_id,)))
        return self.next_public_ip

    def get_security_group_id(self, instance_id: str) -> str:
        return "sg-default"

    def authorize_security_group(self, sg_id: str, ports: list[int], cidr: str = "0.0.0.0/0") -> None:
        self.calls.append(("authorize_security_group", (sg_id, tuple(ports))))

    def delete_instance(self, instance_id: str) -> None:
        self.calls.append(("delete_instance", (instance_id,)))


@pytest.fixture
def admin_token(tmp_path, monkeypatch) -> str:
    """让 verify_admin_token 读到测试用的 token。

    auth.ADMIN_TOKEN_PATH 是模块级常量；env var 只在 import 时生效，
    所以这里 monkeypatch 常量本身（避免 /opt/vibe-niuma 权限问题）。
    """
    token = "test-token-1234567890abcdef"
    tok_file = tmp_path / "admin.token"
    tok_file.write_text(token)
    from orchestrator import auth
    monkeypatch.setattr(auth, "ADMIN_TOKEN_PATH", str(tok_file))
    return token


@pytest.fixture
def client_with_fake_aliyun(monkeypatch, admin_token):
    """注入 fake 阿里云 client + 关掉 poll 等待 + TestClient。"""
    fake = FakeClient()
    from orchestrator import aliyun_provisioner
    monkeypatch.setattr(
        aliyun_provisioner,
        "_default_client_factory",
        lambda creds: fake,
    )
    monkeypatch.setattr(
        aliyun_provisioner.AliyunProvisioner,
        "POLL_INTERVAL_SECONDS", 0.0,
    )
    from orchestrator.main import app
    return TestClient(app), fake


# ── happy path ──────────────────────────────────────────────────────


def test_provision_ecs_happy_path(client_with_fake_aliyun, admin_token):
    client, fake = client_with_fake_aliyun
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={
            "access_key_id": "LTAIfake123",
            "access_key_secret": "fakefakesecret",
            "region_id": "cn-hangzhou",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["instance_id"] == "i-test-12345"
    assert body["public_ip"] == "47.96.88.1"
    assert body["region_id"] == "cn-hangzhou"
    assert len(body["root_password"]) >= 8
    assert 22 in body["open_ports"]
    assert 9000 in body["open_ports"]
    assert 5100 in body["open_ports"]
    # SDK 被打了 create + allocate + authorize
    methods = [c[0] for c in fake.calls]
    assert "create_instance" in methods
    assert "allocate_public_ip" in methods
    assert "authorize_security_group" in methods


def test_provision_ecs_custom_instance_type(client_with_fake_aliyun, admin_token):
    client, fake = client_with_fake_aliyun
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={
            "access_key_id": "x",
            "access_key_secret": "x",
            "region_id": "cn-shanghai",
            "instance_type": "ecs.g7.large",
        },
    )
    assert resp.status_code == 200
    create_call = next(c for c in fake.calls if c[0] == "create_instance")
    assert create_call[1][0] == "ecs.g7.large"
    assert create_call[1][1] == "cn-shanghai"


# ── 鉴权 ────────────────────────────────────────────────────────────


def test_provision_ecs_requires_admin_token(client_with_fake_aliyun, admin_token):
    client, _fake = client_with_fake_aliyun
    resp = client.post(
        "/admin/provision-ecs",
        json={"access_key_id": "x", "access_key_secret": "x"},
    )
    assert resp.status_code == 401


def test_provision_ecs_rejects_wrong_admin_token(client_with_fake_aliyun, admin_token):
    client, _fake = client_with_fake_aliyun
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": "wrong-token-aaaaaaaaaaaaaaaa"},
        json={"access_key_id": "x", "access_key_secret": "x"},
    )
    assert resp.status_code == 401


# ── 校验 ────────────────────────────────────────────────────────────


def test_provision_ecs_422_when_empty_access_key(client_with_fake_aliyun, admin_token):
    client, _fake = client_with_fake_aliyun
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={"access_key_id": "", "access_key_secret": "y"},
    )
    # Pydantic min_length=1 拒绝
    assert resp.status_code == 422


# ── 失败传播 ──────────────────────────────────────────────────────


# ── M2.T14 bootstrap 链 ───────────────────────────────────────────


@dataclass
class _FakeSSH:
    """与 test_ecs_bootstrap.FakeSSH 同形；inline 避免跨 test 文件 import。"""
    default_result: tuple[int, str, str] = (0, "", "")
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def connect(self, target, timeout):
        self.calls.append(("connect", (target.host, target.username, timeout)))

    def exec(self, command, timeout):
        self.calls.append(("exec", (command, timeout)))
        return self.default_result

    def close(self):
        self.calls.append(("close", ()))


def test_provision_ecs_with_bootstrap_chains_ssh(monkeypatch, admin_token):
    """bootstrap=true 时端点应自动 ssh 跑 ecs-bootstrap.sh 并返 admin_token。"""
    from orchestrator import aliyun_provisioner, ecs_bootstrap

    # 阿里云 fake：开机成功
    fake_aliyun = FakeClient()
    monkeypatch.setattr(
        aliyun_provisioner,
        "_default_client_factory",
        lambda creds: fake_aliyun,
    )
    monkeypatch.setattr(aliyun_provisioner.AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.0)

    # ssh fake：返回带 Admin Token 的 stdout
    fake_ssh = _FakeSSH(default_result=(0,
        "vibe-niuma 部署完成\n"
        "Orchestrator URL: http://47.96.88.1:9000\n"
        "Admin Token: bootstrap-token-xyz\n", ""))
    monkeypatch.setattr(
        ecs_bootstrap, "_default_ssh_factory",
        lambda: fake_ssh,
    )

    from orchestrator.main import app
    client = TestClient(app)
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={
            "access_key_id": "LTAIfake",
            "access_key_secret": "fakefake",
            "region_id": "cn-hangzhou",
            "bootstrap": True,
            "deepseek_api_key": "sk-deepseek-xxx",
            "dashscope_api_key": "sk-dashscope-yyy",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bootstrap"]["ok"] is True
    assert body["bootstrap"]["admin_token"] == "bootstrap-token-xyz"
    assert body["bootstrap"]["orchestrator_url"] == "http://47.96.88.1:9000"

    # ssh 被打了：连接 + exec ecs-bootstrap.sh
    methods = [c[0] for c in fake_ssh.calls]
    assert methods[0] == "connect"
    exec_calls = [c for c in fake_ssh.calls if c[0] == "exec"]
    cmd_text = " ".join(c[1][0] for c in exec_calls)
    assert "ecs-bootstrap.sh" in cmd_text
    assert "sk-deepseek-xxx" in cmd_text


def test_provision_ecs_bootstrap_requires_deepseek_key(monkeypatch, admin_token):
    """bootstrap=true 但没传 deepseek_api_key → 422。"""
    from orchestrator import aliyun_provisioner

    monkeypatch.setattr(
        aliyun_provisioner,
        "_default_client_factory",
        lambda creds: FakeClient(),
    )
    monkeypatch.setattr(aliyun_provisioner.AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.0)

    from orchestrator.main import app
    client = TestClient(app)
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={
            "access_key_id": "x",
            "access_key_secret": "y",
            "bootstrap": True,
            # 故意没传 deepseek_api_key
        },
    )
    assert resp.status_code == 422
    assert "deepseek_api_key" in resp.json()["detail"]


def test_provision_ecs_bootstrap_failure_returns_200_with_error(monkeypatch, admin_token):
    """bootstrap 失败时不应整个请求 500 —— ECS 已经开了，业务员要看到失败原因。"""
    from orchestrator import aliyun_provisioner, ecs_bootstrap

    monkeypatch.setattr(
        aliyun_provisioner,
        "_default_client_factory",
        lambda creds: FakeClient(),
    )
    monkeypatch.setattr(aliyun_provisioner.AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.0)

    failing_ssh = _FakeSSH(default_result=(1, "", "apt-get failed: 网络不通"))
    monkeypatch.setattr(
        ecs_bootstrap, "_default_ssh_factory",
        lambda: failing_ssh,
    )

    from orchestrator.main import app
    client = TestClient(app)
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={
            "access_key_id": "x",
            "access_key_secret": "y",
            "bootstrap": True,
            "deepseek_api_key": "sk-x",
        },
    )
    assert resp.status_code == 200  # ECS 起来了，端点不该 500
    body = resp.json()
    assert body["bootstrap"]["ok"] is False
    assert "网络不通" in body["bootstrap"]["error"]
    # 但 ECS 信息要全 —— 业务员还能手动 ssh 上去重试
    assert body["public_ip"] == "47.96.88.1"
    assert body["root_password"]


def test_provision_ecs_500_on_provision_error(monkeypatch, admin_token):
    """provisioner 抛 ProvisionError → 500 + detail。"""
    from orchestrator import aliyun_provisioner

    class FailingClient(FakeClient):
        def create_instance(self, spec, region_id):
            self.calls.append(("create_instance", (spec.instance_type, region_id)))
            raise aliyun_provisioner.ProvisionError("配额不足")

    monkeypatch.setattr(
        aliyun_provisioner,
        "_default_client_factory",
        lambda creds: FailingClient(),
    )
    monkeypatch.setattr(aliyun_provisioner.AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.0)
    from orchestrator.main import app
    client = TestClient(app)
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={"access_key_id": "x", "access_key_secret": "y"},
    )
    assert resp.status_code == 500
    assert "配额不足" in resp.json()["detail"]
