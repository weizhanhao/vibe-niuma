"""Plan 6 Task 3 —— /admin/config GET + PUT 端点。

设计要点：
- `APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_token)])`
  所有路由统一鉴权 —— X-Admin-Token 头错/缺都 401。
- GET 永远把 *_api_key 脱敏（返回 null + *_api_key_set: bool）。
- PUT 用 Pydantic AdminConfigUpdateIn 校验 body；StaleVersionError → 409。
- 副作用：
    * 任一 *_api_key 字段实际变了 → systemctl restart vibe-niuma-llm-proxy
      （subprocess.run, check=False, timeout=10）；
    * 其它字段（dev_runner / dev_model / vision_model / demo_repo_path /
      preview_backend_url）变了 → 仅 `get_settings.cache_clear()`。

不动 SystemConfig schema 的 `version`，由 SystemConfigRepository.update 负责自增。
"""
from __future__ import annotations

import logging
import subprocess
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from orchestrator.auth import verify_admin_token
from orchestrator.config import get_settings
from orchestrator.db import get_db
from orchestrator.models import SystemConfig
from orchestrator.system_config import (
    StaleVersionError,
    SystemConfigRepository,
)

logger = logging.getLogger("orchestrator.admin")


# 密钥字段集合 —— GET 脱敏 + PUT 触发 LiteLLM 重启的依据。
API_KEY_FIELDS = ("deepseek_api_key", "dashscope_api_key", "anthropic_api_key")

# 非密钥字段（值变了只 invalidate settings 缓存，不重启服务）。
PLAIN_FIELDS = (
    "dev_runner",
    "dev_model",
    "vision_model",
    "demo_repo_path",
    "preview_backend_url",
)

# LiteLLM systemd 服务名 —— 部署在 ECS 上的 vibe-niuma-llm-proxy.service。
LITELLM_SERVICE = "vibe-niuma-llm-proxy"


# ──────────────────────── Pydantic schemas ────────────────────────


class AdminConfigUpdateIn(BaseModel):
    """PUT body：config 是 partial patch（字段都可选，client 没发的字段不更新）。

    Pydantic 校验 dev_runner 枚举；其它字段宽松（DB 列宽限定即可）。
    """

    dev_runner: Annotated[
        Literal["opencode", "claude-code"] | None, Field(default=None)
    ] = None
    dev_model: str | None = None
    vision_model: str | None = None
    deepseek_api_key: str | None = None
    dashscope_api_key: str | None = None
    anthropic_api_key: str | None = None
    demo_repo_path: str | None = None
    preview_backend_url: str | None = None


class AdminConfigPutBody(BaseModel):
    config: AdminConfigUpdateIn
    expectedVersion: int = Field(ge=0)


# ──────────────────────── 序列化 ────────────────────────


def _serialize_config(cfg: SystemConfig) -> dict:
    """SystemConfig → response dict，*_api_key 永远脱敏。

    *_api_key 返回 null；新增 *_api_key_set: bool 让 UI 显示「已设置/未设置」。
    """
    out = {
        "dev_runner": cfg.dev_runner,
        "dev_model": cfg.dev_model,
        "vision_model": cfg.vision_model,
        "demo_repo_path": cfg.demo_repo_path,
        "preview_backend_url": cfg.preview_backend_url,
    }
    for field in API_KEY_FIELDS:
        value = getattr(cfg, field)
        out[field] = None  # 永远不回显密钥本体
        out[f"{field}_set"] = bool(value)
    return out


# ──────────────────────── 副作用 ────────────────────────


def _restart_litellm() -> None:
    """systemctl restart vibe-niuma-llm-proxy —— check=False 不阻塞响应。

    本地 dev 环境没有 systemctl 会抛 FileNotFoundError；捕获后只记日志。
    """
    try:
        subprocess.run(
            ["systemctl", "restart", LITELLM_SERVICE],
            check=False,
            timeout=10,
        )
        logger.info("systemctl restart %s issued", LITELLM_SERVICE)
    except FileNotFoundError:
        logger.warning(
            "systemctl 不存在（本地 dev？），跳过 %s 重启", LITELLM_SERVICE
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("systemctl restart %s 失败: %s", LITELLM_SERVICE, exc)


# ──────────────────────── 路由 ────────────────────────


router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(verify_admin_token)],
    tags=["admin"],
)


@router.get("/config")
def get_admin_config(db: Session = Depends(get_db)) -> dict:
    repo = SystemConfigRepository(db)
    cfg = repo.get_or_create()
    return {
        "config": _serialize_config(cfg),
        "version": cfg.version,
    }


@router.put("/config")
def put_admin_config(
    body: AdminConfigPutBody,
    db: Session = Depends(get_db),
) -> dict:
    repo = SystemConfigRepository(db)
    before = repo.get_or_create()
    # 抓 patch 前的所有可比对字段快照 —— SQLAlchemy 同一行的 Python 对象
    # 在 update 后会就地被改，所以必须先抓 immutable snapshot。
    before_snapshot = {
        field: getattr(before, field)
        for field in (*API_KEY_FIELDS, *PLAIN_FIELDS)
    }

    # exclude_unset → 只保留 client 明确发的字段；
    # 没发的字段就不在 patch 里，repo.update 不会动它。
    patch = body.config.model_dump(exclude_unset=True)

    try:
        updated = repo.update(patch, expected_version=body.expectedVersion)
    except StaleVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"stale version: {exc}",
        ) from exc

    # 判定副作用：哪些字段真改了？
    key_changed = any(
        before_snapshot[f] != getattr(updated, f) for f in API_KEY_FIELDS
    )
    plain_changed = any(
        before_snapshot[f] != getattr(updated, f) for f in PLAIN_FIELDS
    )

    restarted_services: list[str] = []
    if key_changed:
        _restart_litellm()
        restarted_services.append(LITELLM_SERVICE)

    # 任一字段变了 → invalidate settings 缓存；下次 callers 读 settings.xxx
    # 走 Settings() 重新加载（DB 主导分支落地后会从 system_config 行重读）。
    if key_changed or plain_changed:
        get_settings.cache_clear()

    return {
        "config": _serialize_config(updated),
        "version": updated.version,
        "restartedServices": restarted_services,
    }
