"""Plan 11 M3.T18 —— /health 健康检查 module。

业务员视角：右上角指示灯有 3 态
- ok（绿）：所有 services 都 ok
- yellow：非核心 service down（llm_proxy / main_demo）→ degraded
- red：核心依赖 mysql / orchestrator 自身 down

服务：
- orchestrator：进程自身能跑就 ok
- mysql：SELECT 1 能跑就 ok（用 session_factory 复用既有连接池）
- llm_proxy：vibe-niuma-llm-proxy 4000 端口 GET / 200 就 ok
- main_demo：业务员预览容器 5173 端口 GET / 200 就 ok

约束：
- 不在 /health 里跑会阻塞超过 ~2s 的探测；每个 service 都有短 timeout
- 没配 URL 的 service 返回 'unknown' 而不是 'down'
- 用 httpx.AsyncClient + 可注入 transport，测试不打真网络
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Optional

import httpx
from sqlalchemy import text

logger = logging.getLogger("orchestrator.health")

ServiceStatus = Literal["ok", "down", "unknown"]
HealthStatus = Literal["ok", "yellow", "red"]


@dataclass
class HealthPayload:
    status: HealthStatus
    services: dict[str, ServiceStatus]
    uptime_seconds: int
    last_cr_at: Optional[str] = None
    last_error: Optional[str] = None
    version: str = "dev"


# ── 单 service 检查 ─────────────────────────────────────────────


def check_mysql(session_factory: Optional[Callable[[], Any]]) -> ServiceStatus:
    """SELECT 1 + 短 timeout。session_factory 为 None → unknown。"""
    if session_factory is None:
        return "unknown"
    try:
        session = session_factory()
        try:
            session.execute(text("SELECT 1")).scalar()
            return "ok"
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("mysql 健康检查失败: %s", exc)
        return "down"


async def check_http_service(
    url: str,
    *,
    timeout: float = 2.0,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> ServiceStatus:
    """GET url，200-399 视作 ok；其它/异常 down；url 空 unknown。"""
    if not url:
        return "unknown"
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as c:
            r = await c.get(url)
            return "ok" if 200 <= r.status_code < 400 else "down"
    except Exception as exc:  # noqa: BLE001
        logger.debug("http 健康检查 %s 失败: %s", url, exc)
        return "down"


def last_change_request_iso(
    session_factory: Optional[Callable[[], Any]],
) -> Optional[str]:
    """最近一条 CR 的 created_at ISO；无记录或失败返 None。"""
    if session_factory is None:
        return None
    try:
        from orchestrator.models import ChangeRequest
        session = session_factory()
        try:
            cr = (
                session.query(ChangeRequest)
                .order_by(ChangeRequest.created_at.desc())
                .first()
            )
            if cr is None or cr.created_at is None:
                return None
            ts: datetime = cr.created_at
            return ts.isoformat(timespec="seconds")
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("last_cr 读取失败: %s", exc)
        return None


# ── 汇总 ────────────────────────────────────────────────────────


_CORE_SERVICES = ("orchestrator", "mysql")


def _aggregate_status(services: dict[str, ServiceStatus]) -> HealthStatus:
    """核心 down → red；非核心 down → yellow；都 ok/unknown → ok。"""
    for name in _CORE_SERVICES:
        if services.get(name) == "down":
            return "red"
    for status in services.values():
        if status == "down":
            return "yellow"
    return "ok"


async def build_health_payload(
    *,
    start_time_monotonic: float,
    session_factory: Optional[Callable[[], Any]],
    llm_proxy_url: str,
    main_demo_url: str,
    last_error: Optional[str] = None,
    http_transport: Optional[httpx.AsyncBaseTransport] = None,
) -> HealthPayload:
    services: dict[str, ServiceStatus] = {
        "orchestrator": "ok",
        "mysql": check_mysql(session_factory),
        "llm_proxy": await check_http_service(
            llm_proxy_url, timeout=2.0, transport=http_transport,
        ),
        "main_demo": await check_http_service(
            main_demo_url, timeout=2.0, transport=http_transport,
        ),
    }
    return HealthPayload(
        status=_aggregate_status(services),
        services=services,
        uptime_seconds=int(time.monotonic() - start_time_monotonic),
        last_cr_at=last_change_request_iso(session_factory),
        last_error=last_error,
    )
