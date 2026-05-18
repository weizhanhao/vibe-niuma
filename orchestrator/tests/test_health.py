"""Plan 11 M3.T18 health module 单测 —— 不依赖 DB/网络，全 fake。"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import pytest

from orchestrator.health import (
    HealthPayload,
    ServiceStatus,
    build_health_payload,
    check_http_service,
    check_mysql,
    last_change_request_iso,
)


# ── check_http_service ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_http_service_ok_when_200():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")
    transport = httpx.MockTransport(handler)
    result = await check_http_service(
        "http://x/health", timeout=0.5, transport=transport,
    )
    assert result == "ok"


@pytest.mark.asyncio
async def test_check_http_service_down_when_5xx():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)
    transport = httpx.MockTransport(handler)
    result = await check_http_service(
        "http://x", timeout=0.5, transport=transport,
    )
    assert result == "down"


@pytest.mark.asyncio
async def test_check_http_service_down_when_timeout():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect refused")
    transport = httpx.MockTransport(handler)
    result = await check_http_service(
        "http://x", timeout=0.5, transport=transport,
    )
    assert result == "down"


@pytest.mark.asyncio
async def test_check_http_service_unknown_when_url_empty():
    """没配 URL → unknown（不是 down，因为根本没让它探测）"""
    result = await check_http_service("", timeout=0.5)
    assert result == "unknown"


# ── check_mysql ─────────────────────────────────────────────


def test_check_mysql_ok_when_select_1_succeeds():
    sf = MagicMock()
    session = MagicMock()
    session.__enter__ = lambda self: session
    session.__exit__ = lambda self, *a: False
    session.execute = MagicMock(return_value=MagicMock(scalar=lambda: 1))
    sf.return_value = session
    assert check_mysql(sf) == "ok"


def test_check_mysql_down_when_exception():
    sf = MagicMock()
    sf.side_effect = RuntimeError("connection refused")
    assert check_mysql(sf) == "down"


def test_check_mysql_unknown_when_no_factory():
    assert check_mysql(None) == "unknown"


# ── last_change_request_iso ──────────────────────────────


def test_last_change_request_iso_returns_iso():
    session = MagicMock()
    session.__enter__ = lambda self: session
    session.__exit__ = lambda self, *a: False
    from datetime import datetime
    cr = MagicMock()
    cr.created_at = datetime(2026, 5, 18, 17, 30, 0)
    session.query.return_value.order_by.return_value.first.return_value = cr
    iso = last_change_request_iso(lambda: session)
    assert iso == "2026-05-18T17:30:00"


def test_last_change_request_iso_none_when_no_records():
    session = MagicMock()
    session.__enter__ = lambda self: session
    session.__exit__ = lambda self, *a: False
    session.query.return_value.order_by.return_value.first.return_value = None
    iso = last_change_request_iso(lambda: session)
    assert iso is None


def test_last_change_request_iso_none_when_session_factory_missing():
    assert last_change_request_iso(None) is None


# ── build_health_payload ────────────────────────────────


@pytest.mark.asyncio
async def test_build_health_payload_all_ok():
    payload = await build_health_payload(
        start_time_monotonic=time.monotonic() - 123.0,
        session_factory=None,
        llm_proxy_url="",
        main_demo_url="",
    )
    assert isinstance(payload, HealthPayload)
    assert payload.status == "ok"
    assert payload.services["orchestrator"] == "ok"
    assert payload.services["mysql"] == "unknown"
    assert payload.services["llm_proxy"] == "unknown"
    assert payload.services["main_demo"] == "unknown"
    assert payload.uptime_seconds >= 120
    assert payload.last_cr_at is None


@pytest.mark.asyncio
async def test_build_health_payload_status_red_when_mysql_down():
    sf = MagicMock(side_effect=RuntimeError("down"))
    payload = await build_health_payload(
        start_time_monotonic=time.monotonic(),
        session_factory=sf,
        llm_proxy_url="",
        main_demo_url="",
    )
    assert payload.services["mysql"] == "down"
    assert payload.status == "red"


@pytest.mark.asyncio
async def test_build_health_payload_status_yellow_when_llm_proxy_down():
    """llm_proxy 挂：业务员还能聊天但不能 CR -> yellow（degraded）"""
    def handler(req): return httpx.Response(503)
    transport = httpx.MockTransport(handler)
    payload = await build_health_payload(
        start_time_monotonic=time.monotonic(),
        session_factory=None,
        llm_proxy_url="http://llm/health",
        main_demo_url="",
        http_transport=transport,
    )
    assert payload.services["llm_proxy"] == "down"
    assert payload.status == "yellow"


@pytest.mark.asyncio
async def test_service_status_typed():
    """ServiceStatus 是 'ok'/'down'/'unknown' 三态字面量"""
    s: ServiceStatus = "ok"
    assert s in ("ok", "down", "unknown")
