"""ecs_bootstrap 测试 —— FakeSSH，不打真 ssh。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from orchestrator.ecs_bootstrap import (
    BootstrapError,
    BootstrapResult,
    EcsBootstrapper,
    SshTarget,
)


# ── FakeSSH ──────────────────────────────────────────────────────


@dataclass
class FakeSSH:
    """脚本化 ssh client：可控连接/命令结果、记录所有调用。"""
    connect_raises: Optional[Exception] = None
    # command 子串 → (exit_code, stdout, stderr)
    command_results: dict[str, tuple[int, str, str]] = field(default_factory=dict)
    default_result: tuple[int, str, str] = (0, "", "")
    calls: list[tuple[str, tuple]] = field(default_factory=list)
    is_open: bool = False

    def connect(self, target: SshTarget, timeout: float) -> None:
        self.calls.append(("connect", (target.host, target.username, timeout)))
        if self.connect_raises:
            raise self.connect_raises
        self.is_open = True

    def exec(self, command: str, timeout: float) -> tuple[int, str, str]:
        self.calls.append(("exec", (command, timeout)))
        for key, result in self.command_results.items():
            if key in command:
                return result
        return self.default_result

    def close(self) -> None:
        self.calls.append(("close", ()))
        self.is_open = False


@pytest.fixture
def target() -> SshTarget:
    return SshTarget(
        host="47.96.1.2",
        username="root",
        password="Aa1Bb2Cc3Dd4!!",
    )


# ── 数据契约 ──────────────────────────────────────────────────────


def test_ssh_target_rejects_empty_fields():
    with pytest.raises(ValueError, match="host"):
        SshTarget(host="", username="root", password="x")
    with pytest.raises(ValueError, match="username"):
        SshTarget(host="1.2.3.4", username="", password="x")
    with pytest.raises(ValueError, match="password"):
        SshTarget(host="1.2.3.4", username="root", password="")


# ── 主路径 ──────────────────────────────────────────────────────────


def test_bootstrap_happy_path(target):
    fake = FakeSSH()
    b = EcsBootstrapper(ssh_factory=lambda: fake)
    result = b.bootstrap(
        target,
        deepseek_key="sk-deepseek-fake",
        dashscope_key="sk-dashscope-fake",
        public_host="47.96.1.2",
    )
    assert isinstance(result, BootstrapResult)
    assert result.ok is True
    methods = [c[0] for c in fake.calls]
    assert methods[0] == "connect"
    assert methods[-1] == "close"
    exec_calls = [c for c in fake.calls if c[0] == "exec"]
    assert len(exec_calls) >= 1
    cmd_text = " ".join(c[1][0] for c in exec_calls)
    assert "ecs-bootstrap.sh" in cmd_text
    assert "--deepseek-key" in cmd_text
    assert "sk-deepseek-fake" in cmd_text
    assert "--dashscope-key" in cmd_text
    # arg 走 shell-quote 包裹（防止业务员粘奇怪字符炸 shell）
    assert "--public-host '47.96.1.2'" in cmd_text


def test_bootstrap_omits_dashscope_when_not_provided(target):
    fake = FakeSSH()
    b = EcsBootstrapper(ssh_factory=lambda: fake)
    b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host="1.2.3.4")
    cmd_text = " ".join(c[1][0] for c in fake.calls if c[0] == "exec")
    assert "--deepseek-key" in cmd_text
    assert "--dashscope-key" not in cmd_text


def test_bootstrap_omits_public_host_when_not_provided(target):
    """缺 public_host 时让 ecs-bootstrap.sh 自己 curl ifconfig.me。"""
    fake = FakeSSH()
    b = EcsBootstrapper(ssh_factory=lambda: fake)
    b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host=None)
    cmd_text = " ".join(c[1][0] for c in fake.calls if c[0] == "exec")
    assert "--public-host" not in cmd_text


def test_bootstrap_extracts_admin_token_from_output(target):
    """ecs-bootstrap.sh 末尾打印 Admin Token: xxx —— 应被解析出来。"""
    fake = FakeSSH(default_result=(0,
        "...\nvibe-niuma 部署完成\n"
        "Orchestrator URL: http://1.2.3.4:9000\n"
        "Admin Token: abc123token456\n"
        "把以上两项粘到扩展即可。\n", ""))
    b = EcsBootstrapper(ssh_factory=lambda: fake)
    result = b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host="1.2.3.4")
    assert result.admin_token == "abc123token456"
    assert result.orchestrator_url == "http://1.2.3.4:9000"


def test_bootstrap_handles_missing_token_gracefully(target):
    """ecs-bootstrap.sh 在 systemd 还没起好时不打 token；result.admin_token=None 但 ok=True。"""
    fake = FakeSSH(default_result=(0, "完成但 token 文件还没生成\n", ""))
    b = EcsBootstrapper(ssh_factory=lambda: fake)
    result = b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host=None)
    assert result.ok is True
    assert result.admin_token is None


# ── 失败路径 ──────────────────────────────────────────────────────


def test_bootstrap_connect_fails_raises(target):
    fake = FakeSSH(connect_raises=ConnectionRefusedError("ssh 端口未开"))
    b = EcsBootstrapper(ssh_factory=lambda: fake, connect_retries=1)
    with pytest.raises(BootstrapError, match="ssh 连接失败"):
        b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host=None)
    methods = [c[0] for c in fake.calls]
    assert "exec" not in methods


def test_bootstrap_command_nonzero_exit_raises(target):
    fake = FakeSSH(default_result=(1, "", "apt-get update failed: 网络不通"))
    b = EcsBootstrapper(ssh_factory=lambda: fake)
    with pytest.raises(BootstrapError, match="bootstrap 失败.*exit=1"):
        b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host=None)


def test_bootstrap_close_always_called_even_on_exec_failure(target):
    fake = FakeSSH(default_result=(1, "", "boom"))
    b = EcsBootstrapper(ssh_factory=lambda: fake)
    with pytest.raises(BootstrapError):
        b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host=None)
    methods = [c[0] for c in fake.calls]
    assert methods[-1] == "close"


def test_bootstrap_retries_connect_until_ssh_ready(target):
    """新 ECS 起来后 sshd 可能还没就绪，bootstrap 应该 retry connect。"""
    @dataclass
    class FlakySsh(FakeSSH):
        connect_attempts: int = 0

        def connect(self, target, timeout):
            self.connect_attempts += 1
            self.calls.append(("connect", (target.host, target.username, timeout)))
            if self.connect_attempts < 3:
                raise ConnectionRefusedError("sshd 还没起")
            self.is_open = True

    fake = FlakySsh()
    b = EcsBootstrapper(
        ssh_factory=lambda: fake,
        connect_retries=5,
        connect_retry_interval=0.0,
    )
    b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host=None)
    assert fake.connect_attempts == 3


def test_bootstrap_command_does_not_leak_password_in_error(target):
    """exit!=0 时 BootstrapError 不能含 password 或 deepseek key（业务员密钥不入 log）。"""
    fake = FakeSSH(default_result=(1, "", "boom"))
    b = EcsBootstrapper(ssh_factory=lambda: fake)
    with pytest.raises(BootstrapError) as exc_info:
        b.bootstrap(target, deepseek_key="sk-secret-key", dashscope_key=None, public_host=None)
    assert target.password not in str(exc_info.value)
    assert "sk-secret-key" not in str(exc_info.value)


# ── lazy import ──────────────────────────────────────────────────


def test_default_factory_raises_clear_error_when_paramiko_missing(target, monkeypatch):
    """模拟 _RealParamikoSsh 构造时 paramiko import 失败的行为 ——
    业务员看到 ParamikoNotInstalled 装提示，不是 ImportError traceback。"""
    from orchestrator import ecs_bootstrap

    def bad_init(self):
        raise ecs_bootstrap.ParamikoNotInstalled(
            "缺 paramiko，跑：pip install 'orchestrator[bootstrap]'"
        )

    monkeypatch.setattr(ecs_bootstrap, "_RealParamikoSsh", type(
        "X", (object,), {"__init__": bad_init},
    ))
    b = EcsBootstrapper()  # 不传 factory → 用 default
    with pytest.raises(ecs_bootstrap.ParamikoNotInstalled, match="pip install"):
        b.bootstrap(target, deepseek_key="sk-x", dashscope_key=None, public_host=None)
