"""SelfHostedDeploy —— 自建 docker compose 部署（M7 唯一实现）。

云效 / GitHub Actions 只留接口不实现（D10，2026-08-24 确认延后）。
将来接它们是**加一个文件**，因为：
  - 方法签名已定死（deploy/adapter.py）
  - deploy_runs 表 M1 就建了，不用二次迁移
  - CI 守住核心层不 import 具体实现
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import select

from vplatform.core.db import session_scope
from vplatform.core.models import DeployRun
from vplatform.deploy.adapter import DeployError, DeployStatus

logger = logging.getLogger(__name__)


class SelfHostedDeploy:
    """实现 DeployAdapter Protocol。

    每个环境一条 compose 命令，由 Project.config['deploy'] 配置：
        {"test": {"cwd": "/opt/envs/test", "cmd": ["docker","compose","up","-d","--build"]}}
    """

    def __init__(self, *, env_config: dict[str, dict] | None = None,
                 timeout: float = 3600):
        self.env_config = env_config or {}
        self.timeout = timeout
        self._logs: dict[str, list[str]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _cfg(self, env: str) -> dict:
        cfg = self.env_config.get(env)
        if not cfg:
            raise DeployError(f"环境 {env!r} 没有配置部署命令")
        return cfg

    async def trigger(self, *, project_id: str, env: str, ref: str, meta: dict) -> str:
        cfg = self._cfg(env)
        with session_scope() as s:
            run = DeployRun(project_id=project_id, env=env, ref=ref,
                            adapter="selfhosted", state="queued", meta=meta or {})
            s.add(run)
            s.flush()
            run_id = run.id

        self._logs[run_id] = []
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id, cfg, ref))
        return run_id

    async def _execute(self, run_id: str, cfg: dict, ref: str) -> None:
        import os

        self._mark(run_id, "running", started=True)
        argv = list(cfg.get("cmd") or [])
        if not argv:
            self._mark(run_id, "failed", detail="cmd 为空")
            return
        env = {**os.environ, **(cfg.get("env") or {}), "DEPLOY_REF": ref}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cfg.get("cwd"), env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            assert proc.stdout is not None
            # **整体超时包住读日志**。之前 readline 无超时，
            # wait_for 只包 proc.wait()，而它要等 stdout EOF 之后才执行 ——
            # 一个卡住不输出也不退出的 compose（等镜像、等端口）会让协程
            # 永久挂起，DeployRun 永远停在 running，self.timeout 形同虚设。
            async def _pump() -> None:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    self._logs.setdefault(run_id, []).append(
                        line.decode("utf-8", "replace").rstrip())
                await proc.wait()

            try:
                await asyncio.wait_for(_pump(), timeout=self.timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise
        except asyncio.TimeoutError:
            self._mark(run_id, "failed", detail=f"部署超时（{self.timeout}s）")
            return
        except Exception as exc:  # noqa: BLE001
            self._mark(run_id, "failed", detail=f"{type(exc).__name__}: {exc}")
            return
        self._mark(run_id, "succeeded" if proc.returncode == 0 else "failed",
                   detail=f"rc={proc.returncode}")

    def _mark(self, run_id: str, state: str, *, detail: str = "",
              started: bool = False) -> None:
        with session_scope() as s:
            run = s.get(DeployRun, run_id)
            if run is None:
                return
            run.state = state
            if detail:
                run.meta = {**(run.meta or {}), "detail": detail}
            if started:
                run.started_at = datetime.utcnow()
            if state in ("succeeded", "failed", "cancelled"):
                run.finished_at = datetime.utcnow()

    async def status(self, deploy_run_id: str) -> DeployStatus:
        with session_scope() as s:
            run = s.get(DeployRun, deploy_run_id)
            if run is None:
                raise DeployError(f"deploy_run {deploy_run_id} 不存在")
            return DeployStatus(state=run.state, external_id=run.external_id,
                                external_url=run.external_url,
                                started_at=run.started_at, finished_at=run.finished_at,
                                detail=(run.meta or {}).get("detail", ""),
                                meta=run.meta or {})

    async def logs(self, deploy_run_id: str) -> AsyncIterator[str]:
        i = 0
        while True:
            lines = self._logs.get(deploy_run_id, [])
            while i < len(lines):
                yield lines[i]
                i += 1
            st = await self.status(deploy_run_id)
            if st.terminal and i >= len(self._logs.get(deploy_run_id, [])):
                return
            await asyncio.sleep(0.2)

    async def cancel(self, deploy_run_id: str) -> None:
        task = self._tasks.get(deploy_run_id)
        if task and not task.done():
            task.cancel()
        self._mark(deploy_run_id, "cancelled", detail="人工取消")


def latest_by_env(project_id: str) -> dict[str, DeployStatus]:
    """每个环境最近一次部署 —— 环境页要用（§11）。"""
    out: dict[str, DeployStatus] = {}
    with session_scope() as s:
        for env in ("preview", "test", "prod"):
            run = s.execute(
                select(DeployRun)
                .where(DeployRun.project_id == project_id, DeployRun.env == env)
                .order_by(DeployRun.created_at.desc()).limit(1)
            ).scalar_one_or_none()
            if run is not None:
                out[env] = DeployStatus(state=run.state, external_url=run.external_url,
                                        started_at=run.started_at,
                                        finished_at=run.finished_at,
                                        meta=run.meta or {})
    return out
