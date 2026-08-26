"""DeployAdapter（D10）—— 接口现在就定死，实现只做自建。

边界：**vibe-niuma 管到「把改动安全地合进汇流分支」，之后交给 CD。**
内环（并行 agent 调度）云效给不了；外环（构建部署）云效很成熟，自己造是浪费。

实现方**不得向外泄漏自己的平台概念**（pipelineId / workflow / job name），
一律收在 config 里 —— 这样换实现是加一个文件，不是改架构。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

# 环境分层（D11）：预览 per 需求临时；测试 per 空间长驻；生产人工闸门
ENV_PREVIEW, ENV_TEST, ENV_PROD = "preview", "test", "prod"
ENVS = (ENV_PREVIEW, ENV_TEST, ENV_PROD)

STATES = ("queued", "running", "succeeded", "failed", "cancelled")


@dataclass
class DeployStatus:
    state: str
    external_id: str | None = None
    external_url: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.state in ("succeeded", "failed", "cancelled")


class DeployError(RuntimeError):
    pass


@runtime_checkable
class DeployAdapter(Protocol):
    async def trigger(self, *, project_id: str, env: str, ref: str,
                      meta: dict) -> str: ...           # → 本地 deploy_run_id，非平台 ID

    async def status(self, deploy_run_id: str) -> DeployStatus: ...

    def logs(self, deploy_run_id: str) -> AsyncIterator[str]: ...

    async def cancel(self, deploy_run_id: str) -> None: ...
