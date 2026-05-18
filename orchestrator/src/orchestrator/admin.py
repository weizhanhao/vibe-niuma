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


# ── Plan 11 · M2.T12 阿里云 ECS 自动开机端点 ──────────────────────


class ProvisionEcsIn(BaseModel):
    """业务员的 access key 直接进 body —— 只在本次请求里活，不入 DB。

    UI 上业务员粘到 wizard 表单 → 立即 POST 这里 → 后续都用拿回来的
    public_ip + admin_token，access key 销毁。
    """
    access_key_id: str = Field(min_length=1)
    access_key_secret: str = Field(min_length=1)
    region_id: str = Field(default="cn-hangzhou", min_length=1)
    instance_type: str | None = Field(
        default=None,
        description="ECS 规格；缺省 ecs.t6-c1m4.large (4C8G)",
    )


@router.post("/provision-ecs")
def provision_ecs(payload: ProvisionEcsIn) -> dict:
    """用业务员的 access key 自动开一台 ECS：
    create_instance → 等 Running → 分配公网 IP → 配安全组 22/9000/5100-5199。
    返回 instance_id + public_ip + 临时 root 密码（业务员 / 后续 ssh 用，跑完 bootstrap 销毁）。

    失败任何阶段：
    - T15 会接到这里做自动 rollback（DeleteInstance）
    - 错误归一化成 4xx：401 auth / 422 quota / 500 unknown
    """
    from orchestrator.aliyun_provisioner import (
        AliyunAuthError,
        AliyunCredentials,
        AliyunProvisioner,
        AliyunQuotaError,
        AliyunSDKNotInstalled,
        EcsSpec,
        ProvisionError,
    )

    try:
        creds = AliyunCredentials(
            access_key_id=payload.access_key_id,
            access_key_secret=payload.access_key_secret,
            region_id=payload.region_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    spec_kwargs: dict = {}
    if payload.instance_type:
        spec_kwargs["instance_type"] = payload.instance_type
    spec = EcsSpec(**spec_kwargs)

    provisioner = AliyunProvisioner(creds)

    try:
        result = provisioner.provision(spec)
    except AliyunSDKNotInstalled as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except AliyunAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except AliyunQuotaError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ProvisionError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("provision-ecs 未知错误")
        raise HTTPException(status_code=500, detail=f"开机失败：{exc}")

    # 注意：返了密码！调用方（扩展 wizard）拿到后立即 ssh 跑 bootstrap，
    # 完成后业务员侧把密码从内存里清。不入 DB，不进 log。
    return {
        "instance_id": result.instance_id,
        "public_ip": result.public_ip,
        "region_id": result.region_id,
        "root_password": result.password,
        "open_ports": result.open_ports,
        "next_step": "ssh root@<public_ip> 然后跑 ecs-bootstrap.sh"
                     "（扩展 wizard 会自动接力做这步）",
    }
