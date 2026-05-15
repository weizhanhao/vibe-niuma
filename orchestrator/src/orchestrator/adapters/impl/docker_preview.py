"""DockerPreviewAdapter —— 每个变更请求一个 docker 容器，端口在配置区间内分配。

MVP 范围（设计文档 §6 + Plan 3 Task 5）：只起前端容器（vite dev server）；
后端走共享实例。这样最快、够 MVP 演示。若 demo 改动常涉及后端，再升级为 compose
整套。

build / run / health-check 都用 subprocess 调 `docker` CLI，包成 asyncio.to_thread。
端口分配是进程内记账（adapter 实例自己保留 used_ports），release 在 teardown。
"""
from __future__ import annotations

import asyncio
import socket
import subprocess
import time
import uuid
from urllib.request import Request, urlopen

from orchestrator.adapters.types import LogSink, PreviewInstance


_DOCKER_BUILD_TIMEOUT = 300
_DOCKER_RUN_TIMEOUT = 60
_HEALTH_TIMEOUT = 60


class DockerPreviewAdapter:
    """实现 PreviewAdapter Protocol。

    构造参数：
        port_min/port_max — 可分配端口区间
        preview_host      — 拼对外预览 URL 的主机（本地 localhost / ECS 公网地址）
        docker_network    — 容器加入哪张网络（默认 bridge）
        internal_port     — 容器里前端监听端口（demo 的 vite dev 是 5173）
    """

    def __init__(
        self,
        *,
        port_min: int = 5100,
        port_max: int = 5199,
        preview_host: str = "localhost",
        docker_network: str = "bridge",
        internal_port: int = 5173,
    ):
        self._port_min = port_min
        self._port_max = port_max
        self._preview_host = preview_host
        self._docker_network = docker_network
        self._internal_port = internal_port
        self._used_ports: dict[str, int] = {}  # handle → port

    # ── serve ───────────────────────────────────────────────────────
    async def serve(
        self, repo_path: str, branch: str, *, log: LogSink | None = None
    ) -> PreviewInstance:
        """构建 + 起容器；Phase F 把 docker build / run 输出实时推到 log。"""
        port = self._allocate_port()
        preview_id = uuid.uuid4().hex[:12]
        image = f"doskill-preview-{preview_id}"
        name = image  # 容器名复用镜像名，便于排查

        # 1) build image（前端目录）—— stream 输出
        if log is not None:
            await log("▸ docker build...")
        build_rc, build_log = await self._run_streaming(
            ["docker", "build", "-t", image, "frontend"],
            cwd=repo_path, timeout=_DOCKER_BUILD_TIMEOUT, log=log,
        )
        if build_rc != 0:
            raise RuntimeError(f"docker build 失败 (rc={build_rc})\n{build_log[-2000:]}")

        # 2) run container —— stream 输出
        run_cmd = [
            "docker", "run", "-d",
            "--name", name,
            "--network", self._docker_network,
            "-p", f"{port}:{self._internal_port}",
            image,
        ]
        if log is not None:
            await log("▸ docker run...")
        run_rc, run_log = await self._run_streaming(
            run_cmd, cwd=repo_path, timeout=_DOCKER_RUN_TIMEOUT, log=log,
        )
        if run_rc != 0:
            self._release_port(name)
            raise RuntimeError(f"docker run 失败 (rc={run_rc})\n{run_log[-2000:]}")

        container_id = run_log.strip().splitlines()[-1] if run_log.strip() else name
        self._used_ports[container_id] = port

        # 3) health check —— 轮询 HTTP 直到 200 或超时
        if log is not None:
            await log(f"▸ health check (up to {_HEALTH_TIMEOUT}s)...")
        url = f"http://{self._preview_host}:{port}"
        ok = await asyncio.to_thread(self._wait_http, url, _HEALTH_TIMEOUT)
        if not ok:
            # best-effort 拆掉再抛
            await self.teardown(PreviewInstance(preview_id=preview_id, url=url, handle=container_id))
            raise RuntimeError(f"预览容器在 {_HEALTH_TIMEOUT}s 内未变 healthy: {url}")

        return PreviewInstance(preview_id=preview_id, url=url, handle=container_id)

    # ── teardown ────────────────────────────────────────────────────
    async def teardown(self, instance: PreviewInstance) -> None:
        handle = instance.handle
        # best-effort：错误吞掉、幂等
        await asyncio.to_thread(
            self._run, ["docker", "stop", handle],
            cwd=None, timeout=30, check=False,
        )
        await asyncio.to_thread(
            self._run, ["docker", "rm", "-f", handle],
            cwd=None, timeout=30, check=False,
        )
        self._release_port(handle)

    # ── helpers ─────────────────────────────────────────────────────
    def _allocate_port(self) -> int:
        used = set(self._used_ports.values())
        for port in range(self._port_min, self._port_max + 1):
            if port in used:
                continue
            if _port_free(port):
                return port
        raise RuntimeError(
            f"配置端口区间 {self._port_min}-{self._port_max} 已耗尽，"
            f"已占 {len(used)} 个"
        )

    def _release_port(self, handle: str) -> None:
        self._used_ports.pop(handle, None)

    @staticmethod
    async def _run_streaming(
        cmd: list[str], *, cwd: str | None, timeout: int, log: LogSink | None,
    ) -> tuple[int, str]:
        """async subprocess + 按行 stream；返回 (returncode, 合并 stdout+stderr 文本)。

        - log 非 None 时每行 await log(line)（已截至 200 字符上限由 publish_log 把关）
        - timeout 是整体上限；超了 kill 后回填 124
        - FileNotFoundError → 127（docker 没装的兜底）
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            return 127, f"找不到命令: {exc}"

        collected: list[str] = []

        async def _drain() -> None:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    return
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                collected.append(text)
                if text.strip() and log is not None:
                    try:
                        await log(text)
                    except Exception:
                        pass

        try:
            await asyncio.wait_for(_drain(), timeout=timeout)
            await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass
            return 124, "\n".join(collected) + f"\n[超时 {timeout}s]"

        return proc.returncode or 0, "\n".join(collected)

    @staticmethod
    def _run(cmd, *, cwd, timeout, check=True) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, timeout=timeout,
                capture_output=True, text=True,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if check and proc.returncode != 0:
                return proc.returncode, out
            return proc.returncode, out
        except subprocess.TimeoutExpired as exc:
            return 124, f"超时（{timeout}s）: {exc}"
        except FileNotFoundError as exc:
            return 127, f"找不到命令: {exc}"

    @staticmethod
    def _wait_http(url: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                req = Request(url, method="GET")
                with urlopen(req, timeout=3) as resp:
                    if 200 <= resp.status < 500:
                        return True
            except Exception:
                pass
            time.sleep(0.5)
        return False


def _port_free(port: int) -> bool:
    """OS 层快速探测：尝试 bind，不成则视为占用。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()
