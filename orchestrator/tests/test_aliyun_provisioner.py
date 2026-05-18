"""aliyun_provisioner 测试 —— 全部用 FakeClient，不打真阿里云。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from orchestrator.aliyun_provisioner import (
    AliyunCredentials,
    AliyunProvisioner,
    EcsSpec,
    ProvisionError,
    _compress_port_ranges,
)


# ── FakeClient ──────────────────────────────────────────────────────


@dataclass
class FakeClient:
    """脚本化 client：可控 status 序列、可控失败、记录所有调用。"""
    # 脚本：describe_instance_status 调用 N 次返回这些 status
    status_script: list[str] = field(default_factory=lambda: ["Pending", "Starting", "Running"])
    # 失败注入
    create_raises: Optional[Exception] = None
    ip_raises: Optional[Exception] = None
    sg_raises: Optional[Exception] = None
    # 记录
    calls: list[tuple[str, tuple]] = field(default_factory=list)
    next_instance_id: str = "i-fake-001"
    next_public_ip: str = "47.96.1.2"
    next_sg_id: str = "sg-default-001"

    def create_instance(self, spec: EcsSpec, region_id: str) -> str:
        self.calls.append(("create_instance", (spec.instance_type, region_id)))
        if self.create_raises:
            raise self.create_raises
        return self.next_instance_id

    def describe_instance_status(self, instance_id: str) -> str:
        self.calls.append(("describe_instance_status", (instance_id,)))
        if self.status_script:
            return self.status_script.pop(0)
        return "Running"

    def allocate_public_ip(self, instance_id: str) -> str:
        self.calls.append(("allocate_public_ip", (instance_id,)))
        if self.ip_raises:
            raise self.ip_raises
        return self.next_public_ip

    def get_security_group_id(self, instance_id: str) -> str:
        self.calls.append(("get_security_group_id", (instance_id,)))
        return self.next_sg_id

    def authorize_security_group(self, sg_id: str, ports: list[int], cidr: str = "0.0.0.0/0") -> None:
        self.calls.append(("authorize_security_group", (sg_id, tuple(ports))))
        if self.sg_raises:
            raise self.sg_raises

    def delete_instance(self, instance_id: str) -> None:
        self.calls.append(("delete_instance", (instance_id,)))


@pytest.fixture
def fake_creds() -> AliyunCredentials:
    return AliyunCredentials(
        access_key_id="LTAI5tFAKE",
        access_key_secret="secretsecretsecret",
        region_id="cn-hangzhou",
    )


@pytest.fixture
def speedup_polling(monkeypatch):
    """跑测试时 poll_interval 调到 0 —— 不真睡 5 秒。"""
    monkeypatch.setattr(AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.0)


# ── 数据契约 / 边界 ────────────────────────────────────────────────


def test_credentials_rejects_empty_fields():
    with pytest.raises(ValueError, match="access_key_id"):
        AliyunCredentials(access_key_id="", access_key_secret="x")
    with pytest.raises(ValueError, match="access_key_secret"):
        AliyunCredentials(access_key_id="x", access_key_secret="")
    with pytest.raises(ValueError, match="region_id"):
        AliyunCredentials(access_key_id="x", access_key_secret="x", region_id="")


def test_random_password_meets_aliyun_rules():
    spec = EcsSpec.with_random_password()
    assert len(spec.password) >= 8
    assert len(spec.password) <= 30
    assert any(c.isupper() for c in spec.password)
    assert any(c.islower() for c in spec.password)
    assert any(c.isdigit() for c in spec.password)


def test_compress_port_ranges_merges_contiguous():
    assert _compress_port_ranges([22]) == [(22, 22)]
    assert _compress_port_ranges([22, 9000]) == [(22, 22), (9000, 9000)]
    assert _compress_port_ranges(list(range(5100, 5200))) == [(5100, 5199)]
    assert _compress_port_ranges([22, 5100, 5101, 5102, 9000]) == [
        (22, 22), (5100, 5102), (9000, 9000),
    ]
    assert _compress_port_ranges([]) == []


def test_compress_port_ranges_dedupes_and_sorts():
    assert _compress_port_ranges([9000, 22, 22, 9001]) == [(22, 22), (9000, 9001)]


# ── 主路径 ──────────────────────────────────────────────────────────


def test_provision_happy_path(fake_creds, speedup_polling):
    fake = FakeClient(status_script=["Pending", "Running"])
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    result = p.provision(EcsSpec.with_random_password())

    assert result.instance_id == "i-fake-001"
    assert result.public_ip == "47.96.1.2"
    assert result.region_id == "cn-hangzhou"
    assert 22 in result.open_ports
    assert 9000 in result.open_ports
    assert 5100 in result.open_ports

    # 顺序：create → describe(x N) → allocate_ip → get_sg → authorize_sg
    methods = [c[0] for c in fake.calls]
    assert methods[0] == "create_instance"
    assert "allocate_public_ip" in methods
    assert "authorize_security_group" in methods
    assert methods[-1] == "authorize_security_group"


def test_provision_generates_password_when_spec_empty(fake_creds, speedup_polling):
    fake = FakeClient(status_script=["Running"])
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    result = p.provision(EcsSpec())  # password=""
    assert result.password != ""
    assert len(result.password) >= 8


def test_provision_custom_ports(fake_creds, speedup_polling):
    fake = FakeClient(status_script=["Running"])
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    result = p.provision(EcsSpec.with_random_password(), ports=[8080, 8443])
    assert result.open_ports == [8080, 8443]


# ── 失败路径 ──────────────────────────────────────────────────────


def test_provision_create_fails_does_not_call_subsequent(fake_creds, speedup_polling):
    fake = FakeClient(create_raises=RuntimeError("配额满"))
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    with pytest.raises(RuntimeError, match="配额满"):
        p.provision(EcsSpec.with_random_password())
    # 后续不该调
    methods = [c[0] for c in fake.calls]
    assert methods == ["create_instance"]


def test_provision_status_failed_raises_provision_error(fake_creds, speedup_polling):
    fake = FakeClient(status_script=["Pending", "Failed"])
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    with pytest.raises(ProvisionError, match="异常状态 Failed"):
        p.provision(EcsSpec.with_random_password())


def test_provision_timeout_raises(fake_creds, monkeypatch):
    """status 永远不到 Running → 超时 raise。"""
    # 自定义 fake：describe 永远返 "Pending"，绝不 fallback 到 Running
    class ForeverPendingClient(FakeClient):
        def describe_instance_status(self, instance_id: str) -> str:
            self.calls.append(("describe_instance_status", (instance_id,)))
            return "Pending"

    fake = ForeverPendingClient()
    monkeypatch.setattr(AliyunProvisioner, "POLL_INTERVAL_SECONDS", 0.01)
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    with pytest.raises(ProvisionError, match="Running 超过"):
        p.provision(EcsSpec.with_random_password(), running_timeout=0.05)


def test_provision_ip_fails_after_running(fake_creds, speedup_polling):
    fake = FakeClient(
        status_script=["Running"],
        ip_raises=RuntimeError("EIP 配额满"),
    )
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    # Plan 11 M2.T15：auto_rollback=False 时，IP 失败实例保留（给调试）
    with pytest.raises(RuntimeError, match="EIP 配额满"):
        p.provision(EcsSpec.with_random_password(), auto_rollback=False)
    methods = [c[0] for c in fake.calls]
    assert "create_instance" in methods
    assert "allocate_public_ip" in methods
    assert "authorize_security_group" not in methods
    # auto_rollback=False 不调 delete
    assert "delete_instance" not in methods


def test_provision_auto_rollback_deletes_on_failure(fake_creds, speedup_polling):
    """Plan 11 M2.T15：auto_rollback=True（默认）时，失败应自动 delete。"""
    fake = FakeClient(
        status_script=["Running"],
        ip_raises=RuntimeError("EIP 配额满"),
    )
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    with pytest.raises(RuntimeError, match="EIP 配额满"):
        p.provision(EcsSpec.with_random_password())  # auto_rollback 默认 True
    methods = [c[0] for c in fake.calls]
    assert "create_instance" in methods
    assert "allocate_public_ip" in methods
    # 失败后自动调了 delete_instance
    assert ("delete_instance", ("i-fake-001",)) in fake.calls


def test_provision_auto_rollback_does_not_run_if_create_failed(fake_creds, speedup_polling):
    """create_instance 自己挂的 → 没 instance_id 可删，不该误调 delete。"""
    fake = FakeClient(create_raises=RuntimeError("RAM 权限不足"))
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    with pytest.raises(RuntimeError, match="RAM 权限不足"):
        p.provision(EcsSpec.with_random_password())  # auto_rollback=True
    methods = [c[0] for c in fake.calls]
    assert methods == ["create_instance"]  # 不该有 delete


def test_provision_auto_rollback_silent_if_delete_also_fails(fake_creds, speedup_polling):
    """rollback 自己挂时，原异常仍正常传播（不被 rollback 错误掩盖）。"""
    @dataclass
    class FlakyClient(FakeClient):
        def delete_instance(self, instance_id: str) -> None:
            self.calls.append(("delete_instance", (instance_id,)))
            raise RuntimeError("阿里云此刻 API 也挂了")

    fake = FlakyClient(
        status_script=["Running"],
        ip_raises=RuntimeError("原始 IP 错误"),
    )
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    # 看到的应该是**原始**错误，不是 rollback 的错误
    with pytest.raises(RuntimeError, match="原始 IP 错误"):
        p.provision(EcsSpec.with_random_password())


def test_rollback_calls_delete_instance(fake_creds):
    fake = FakeClient()
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    p.rollback("i-existing")
    assert ("delete_instance", ("i-existing",)) in fake.calls


def test_rollback_swallows_delete_error(fake_creds):
    """rollback 自己挂不应该再传播 —— 已经在错误处理路径上了。"""
    @dataclass
    class FlakyDeleteClient(FakeClient):
        def delete_instance(self, instance_id: str) -> None:
            self.calls.append(("delete_instance", (instance_id,)))
            raise RuntimeError("阿里云此刻也挂了")

    fake = FlakyDeleteClient()
    p = AliyunProvisioner(fake_creds, client_factory=lambda c: fake)
    p.rollback("i-x")  # 不抛即对


# ── lazy SDK ──────────────────────────────────────────────────────


def test_default_factory_raises_clear_error_when_sdk_missing(fake_creds, monkeypatch):
    """没装 SDK 时给业务员看得懂的错误，不是 import traceback。"""
    from orchestrator import aliyun_provisioner

    def bad_init(self, c):
        raise ImportError("alibabacloud_ecs20140526 not found")

    monkeypatch.setattr(aliyun_provisioner, "_RealAliyunEcsClient", type(
        "X", (object,), {"__init__": bad_init},
    ))
    p = AliyunProvisioner(fake_creds)  # 不传 factory → 用 default
    with pytest.raises(aliyun_provisioner.AliyunSDKNotInstalled, match="pip install"):
        p.provision(EcsSpec.with_random_password())
