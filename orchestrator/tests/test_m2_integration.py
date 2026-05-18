"""Plan 11 · M2.T17 M2 集成测试 —— 把 T11-T15 全部串起来：

模拟业务员 POST /admin/provision-ecs (bootstrap=true)：
- AliyunProvisioner.provision 跑全套 create/running/IP/sg
- 成功后 EcsBootstrapper.bootstrap 通过 fake SSH 跑 ecs-bootstrap.sh
- 端点返 admin_token + orchestrator_url 给业务员
- 任何阶段失败：Aliyun 阶段→自动 rollback；bootstrap 阶段→保留 ECS 给业务员调试

不打真阿里云 SDK，不打真 ssh —— 全 fake，跑得快。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from orchestrator.aliyun_provisioner import EcsSpec


# ── fakes ──────────────────────────────────────────────────────────


@dataclass
class FakeAliyunClient:
    """全套 Aliyun ECS client，记录所有 calls 用于 assert。"""
    status_script: list[str] = field(default_factory=lambda: ["Pending", "Running"])
    create_raises: Optional[Exception] = None
    calls: list[tuple[str, tuple]] = field(default_factory=list)
    instance_id: str = "i-m2-integration-001"
    public_ip: str = "47.96.222.1"

    def create_instance(self, spec: EcsSpec, region_id: str) -> str:
        self.calls.append(("create_instance", (spec.instance_type, region_id)))
        if self.create_raises:
            raise self.create_raises
        return self.instance_id

    def describe_instance_status(self, instance_id: str) -> str:
        if self.status_script:
            return self.status_script.pop(0)
        return "Running"

    def allocate_public_ip(self, instance_id: str) -> str:
        self.calls.append(("allocate_public_ip", (instance_id,)))
        return self.public_ip

    def get_security_group_id(self, instance_id: str) -> str:
        return "sg-default"

    def authorize_security_group(self, sg_id: str, ports: list[int], cidr: str = "0.0.0.0/0") -> None:
        self.calls.append(("authorize_security_group", (sg_id, tuple(ports))))

    def delete_instance(self, instance_id: str) -> None:
        self.calls.append(("delete_instance", (instance_id,)))


@dataclass
class FakeSSH:
    default_result: tuple[int, str, str] = (0, "", "")
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    def connect(self, target, timeout):
        self.calls.append(("connect", (target.host, target.username, timeout)))

    def exec(self, command, timeout):
        self.calls.append(("exec", (command, timeout)))
        return self.default_result

    def close(self):
        self.calls.append(("close", ()))


@pytest.fixture
def admin_token(tmp_path, monkeypatch) -> str:
    token = "m2-integration-token-xxxx"
    tok_file = tmp_path / "admin.token"
    tok_file.write_text(token)
    from orchestrator import auth
    monkeypatch.setattr(auth, "ADMIN_TOKEN_PATH", str(tok_file))
    return token


@pytest.fixture
def client_with_fakes(monkeypatch, admin_token):
    fake_aliyun = FakeAliyunClient()
    fake_ssh = FakeSSH(default_result=(0,
        "vibe-niuma 部署完成\n"
        "Orchestrator URL: http://47.96.222.1:9000\n"
        "Admin Token: integration-test-admin-token\n", ""))

    from orchestrator import aliyun_provisioner, ecs_bootstrap
    monkeypatch.setattr(
        aliyun_provisioner, "_default_client_factory",
        lambda creds: fake_aliyun,
    )
    monkeypatch.setattr(aliyun_provisioner.AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(
        ecs_bootstrap, "_default_ssh_factory",
        lambda: fake_ssh,
    )

    from orchestrator.main import app
    return TestClient(app), fake_aliyun, fake_ssh


# ── M2 端到端 ─────────────────────────────────────────────────────


def test_m2_full_chain_provision_plus_bootstrap_succeeds(client_with_fakes, admin_token):
    """业务员粘 access key + bootstrap=true → 拿回 admin_token 一步到位。"""
    client, fake_aliyun, fake_ssh = client_with_fakes
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={
            "access_key_id": "LTAI-m2-test",
            "access_key_secret": "m2-secret-xxx",
            "region_id": "cn-hangzhou",
            "bootstrap": True,
            "deepseek_api_key": "sk-m2-deepseek",
            "dashscope_api_key": "sk-m2-dashscope",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    aliyun_methods = [c[0] for c in fake_aliyun.calls]
    assert aliyun_methods[0] == "create_instance"
    assert "allocate_public_ip" in aliyun_methods
    assert "authorize_security_group" in aliyun_methods
    assert aliyun_methods[-1] == "authorize_security_group"
    assert "delete_instance" not in aliyun_methods

    ssh_methods = [c[0] for c in fake_ssh.calls]
    assert ssh_methods[0] == "connect"
    assert ssh_methods[-1] == "close"
    cmd_text = " ".join(c[1][0] for c in fake_ssh.calls if c[0] == "exec")
    assert "ecs-bootstrap.sh" in cmd_text
    assert "sk-m2-deepseek" in cmd_text
    assert "sk-m2-dashscope" in cmd_text

    assert body["instance_id"] == "i-m2-integration-001"
    assert body["public_ip"] == "47.96.222.1"
    assert body["bootstrap"]["ok"] is True
    assert body["bootstrap"]["admin_token"] == "integration-test-admin-token"
    assert body["bootstrap"]["orchestrator_url"] == "http://47.96.222.1:9000"


def test_m2_aliyun_fail_triggers_rollback_no_ssh(monkeypatch, admin_token):
    """Aliyun 阶段失败：自动 rollback DeleteInstance；不会尝试 ssh。"""
    fake_aliyun = FakeAliyunClient(status_script=[])

    def boom_alloc(instance_id):
        fake_aliyun.calls.append(("allocate_public_ip", (instance_id,)))
        raise RuntimeError("EIP 配额满")
    fake_aliyun.allocate_public_ip = boom_alloc

    fake_ssh = FakeSSH()

    from orchestrator import aliyun_provisioner, ecs_bootstrap
    monkeypatch.setattr(
        aliyun_provisioner, "_default_client_factory",
        lambda creds: fake_aliyun,
    )
    monkeypatch.setattr(aliyun_provisioner.AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.0)
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
            "access_key_id": "x",
            "access_key_secret": "y",
            "bootstrap": True,
            "deepseek_api_key": "sk-x",
        },
    )
    assert resp.status_code == 500
    assert "EIP 配额满" in resp.json()["detail"]

    methods = [c[0] for c in fake_aliyun.calls]
    assert "create_instance" in methods
    assert ("delete_instance", ("i-m2-integration-001",)) in fake_aliyun.calls
    assert fake_ssh.calls == []


def test_m2_bootstrap_fail_does_not_delete_ecs(monkeypatch, admin_token):
    """bootstrap ssh 失败：保留 ECS 给业务员手动调试，不触 rollback。"""
    fake_aliyun = FakeAliyunClient()
    fake_ssh = FakeSSH(default_result=(1, "", "apt-get failed: 网络不通"))

    from orchestrator import aliyun_provisioner, ecs_bootstrap
    monkeypatch.setattr(
        aliyun_provisioner, "_default_client_factory",
        lambda creds: fake_aliyun,
    )
    monkeypatch.setattr(aliyun_provisioner.AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.0)
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
            "access_key_id": "x",
            "access_key_secret": "y",
            "bootstrap": True,
            "deepseek_api_key": "sk-x",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bootstrap"]["ok"] is False
    assert "网络不通" in body["bootstrap"]["error"]
    assert body["public_ip"] == "47.96.222.1"
    assert body["root_password"]

    aliyun_methods = [c[0] for c in fake_aliyun.calls]
    assert "create_instance" in aliyun_methods
    assert "allocate_public_ip" in aliyun_methods
    assert "authorize_security_group" in aliyun_methods
    assert "delete_instance" not in aliyun_methods


def test_m2_no_bootstrap_returns_legacy_shape(client_with_fakes, admin_token):
    """bootstrap=false（或缺省）：兼容老 T12 端点行为，bootstrap 字段为空，
    业务员拿 password 自己 ssh。"""
    client, fake_aliyun, fake_ssh = client_with_fakes
    resp = client.post(
        "/admin/provision-ecs",
        headers={"X-Admin-Token": admin_token},
        json={
            "access_key_id": "x",
            "access_key_secret": "y",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bootstrap"] == {}
    assert body["root_password"]
    assert fake_ssh.calls == []
