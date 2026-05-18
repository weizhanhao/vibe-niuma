"""Plan 11 M2.T14 —— 自动 ssh 到新 ECS 跑 ecs-bootstrap.sh。

设计：
- SshTarget：host / username / password 三件套，构造时校验非空
- SshClient Protocol：DI，prod 走 paramiko，测试走 FakeSSH
- EcsBootstrapper.bootstrap()：
    1. 重试连 ssh（新 ECS 起来后 sshd 可能还没就绪，retry 5 次×3s）
    2. 拼 `curl ... ecs-bootstrap.sh | sudo bash -s -- --deepseek-key X ...`
    3. exec；解析 stdout 末尾的 `Admin Token: xxx` + `Orchestrator URL: ...`
    4. 永远 close（finally）

安全：
- BootstrapError.__str__ 不含 password / api keys（异常消息只贴 host/exit/stderr）
- paramiko 没装时给业务员看得懂的 `ParamikoNotInstalled` 而不是 import traceback
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

logger = logging.getLogger("orchestrator.ecs_bootstrap")

_TOKEN_LINE = re.compile(r"Admin Token:\s*(\S+)")
_URL_LINE = re.compile(r"Orchestrator URL:\s*(\S+)")

# 默认从 GitHub 拉 ecs-bootstrap.sh —— 与 deploy/ecs-bootstrap.sh README 一致
DEFAULT_BOOTSTRAP_URL = (
    "https://raw.githubusercontent.com/weizhanhao/vibe-niuma/main/deploy/ecs-bootstrap.sh"
)


# ── 异常 ─────────────────────────────────────────────────────────


class BootstrapError(RuntimeError):
    """bootstrap 全过程的归一错误。stderr/exit 安全暴露；secret 不入 message。"""


class ParamikoNotInstalled(RuntimeError):
    """paramiko 没装 —— 装提示给业务员。"""


# ── 数据契约 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SshTarget:
    host: str
    username: str
    password: str
    port: int = 22

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host 不能为空")
        if not self.username:
            raise ValueError("username 不能为空")
        if not self.password:
            raise ValueError("password 不能为空")


@dataclass
class BootstrapResult:
    ok: bool
    admin_token: Optional[str] = None
    orchestrator_url: Optional[str] = None
    stdout_tail: str = ""


# ── ssh client Protocol ────────────────────────────────────────


class SshClient(Protocol):
    def connect(self, target: SshTarget, timeout: float) -> None: ...
    def exec(self, command: str, timeout: float) -> tuple[int, str, str]: ...
    def close(self) -> None: ...


# ── paramiko 真实实现（lazy import） ──────────────────────────


class _RealParamikoSsh:
    def __init__(self) -> None:
        try:
            import paramiko  # noqa: F401
        except ImportError as exc:
            raise ParamikoNotInstalled(
                "缺 paramiko，跑：pip install 'orchestrator[bootstrap]'"
                "（或 pip install paramiko）"
            ) from exc
        self._client = None

    def connect(self, target: SshTarget, timeout: float) -> None:
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        self._client = client

    def exec(self, command: str, timeout: float) -> tuple[int, str, str]:
        if self._client is None:
            raise RuntimeError("exec 在 connect 之前调用")
        _stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, out, err

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                logger.debug("ssh close 异常（忽略）", exc_info=True)
            self._client = None


def _default_ssh_factory() -> SshClient:
    return _RealParamikoSsh()


# ── Bootstrapper ────────────────────────────────────────────────


@dataclass
class EcsBootstrapper:
    # None → bootstrap() 调用时再读 module-level _default_ssh_factory
    # 这样 monkeypatch(ecs_bootstrap, "_default_ssh_factory", ...) 能命中
    ssh_factory: Optional[Callable[[], SshClient]] = None
    bootstrap_url: str = DEFAULT_BOOTSTRAP_URL
    connect_timeout: float = 15.0
    exec_timeout: float = 600.0
    connect_retries: int = 10
    connect_retry_interval: float = 3.0

    def bootstrap(
        self,
        target: SshTarget,
        *,
        deepseek_key: str,
        dashscope_key: Optional[str],
        public_host: Optional[str],
    ) -> BootstrapResult:
        if not deepseek_key:
            raise ValueError("deepseek_key 必填")

        factory = self.ssh_factory if self.ssh_factory is not None else _default_ssh_factory
        client = factory()
        try:
            self._connect_with_retry(client, target)
            command = self._build_command(deepseek_key, dashscope_key, public_host)
            logger.info(
                "ssh bootstrap → %s@%s（不打印 command body，含密钥）",
                target.username, target.host,
            )
            exit_code, stdout, stderr = client.exec(command, timeout=self.exec_timeout)
            if exit_code != 0:
                tail = (stderr or stdout)[-500:]
                raise BootstrapError(
                    f"bootstrap 失败 host={target.host} exit={exit_code}: {tail}"
                )
            token = _extract(stdout, _TOKEN_LINE)
            url = _extract(stdout, _URL_LINE)
            return BootstrapResult(
                ok=True,
                admin_token=token,
                orchestrator_url=url,
                stdout_tail=stdout[-2000:],
            )
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                logger.debug("close 异常（忽略）", exc_info=True)

    def _connect_with_retry(self, client: SshClient, target: SshTarget) -> None:
        last: Optional[Exception] = None
        for attempt in range(1, max(1, self.connect_retries) + 1):
            try:
                client.connect(target, timeout=self.connect_timeout)
                return
            except ParamikoNotInstalled:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                logger.info(
                    "ssh 连接 %s 失败（第 %d/%d 次）：%s",
                    target.host, attempt, self.connect_retries, exc,
                )
                if attempt < self.connect_retries:
                    time.sleep(self.connect_retry_interval)
        raise BootstrapError(f"ssh 连接失败 host={target.host}: {last}") from last

    def _build_command(
        self,
        deepseek_key: str,
        dashscope_key: Optional[str],
        public_host: Optional[str],
    ) -> str:
        parts = [
            f"curl -fsSL {self.bootstrap_url}",
            "| sudo bash -s --",
            f"--deepseek-key {_shell_arg(deepseek_key)}",
        ]
        if dashscope_key:
            parts.append(f"--dashscope-key {_shell_arg(dashscope_key)}")
        if public_host:
            parts.append(f"--public-host {_shell_arg(public_host)}")
        return " ".join(parts)


# ── 辅助 ──────────────────────────────────────────────────────────


def _extract(text: str, pattern: re.Pattern[str]) -> Optional[str]:
    m = pattern.search(text or "")
    return m.group(1) if m else None


def _shell_arg(value: str) -> str:
    """单引号包裹 + 转义内部 '；deepseek/dashscope key 是 sk-xxx 格式，安全。"""
    escaped = value.replace("'", "'\\''")
    return f"'{escaped}'"
