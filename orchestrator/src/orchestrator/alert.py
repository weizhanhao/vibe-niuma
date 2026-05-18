"""Plan 11 M3.T21 —— 钉钉 / 飞书 / Discord webhook 通用告警接口。

业务员侧（M3.T22 ReportToDevButton）会让业务员一键报错；orchestrator 收到 /alert
请求后根据 system_config 里配的 webhook URL，自动 detect_client → send。

设计：
- AlertMessage：title + body + 可选 link_url
- 三家各一个 Client 子类，统一 async def send(msg)
- DingTalk 走 HMAC-SHA256（accessToken + 可选 secret）；其它两家无签名
- 失败统一抛 AlertError（业务员 UI 看到「告警发送失败：xxx」而不是 stack）
- 可注入 transport，测试不打真网络
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Optional, Protocol
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger("orchestrator.alert")


class AlertError(RuntimeError):
    """告警发送失败（HTTP 5xx / webhook errcode / 网络）。"""


@dataclass(frozen=True)
class AlertMessage:
    title: str
    body: str
    link_url: Optional[str] = None

    def to_text(self) -> str:
        parts = [self.title, "", self.body]
        if self.link_url:
            parts += ["", f"链接: {self.link_url}"]
        return "\n".join(parts)


# ── 签名 ────────────────────────────────────────────────────────


def sign_dingtalk(secret: str, ts: str) -> str:
    """钉钉机器人签名：HMAC-SHA256(secret, ts+'\\n'+secret) → base64 → url-encode。
    https://open.dingtalk.com/document/robots/customize-robot-security-settings"""
    string_to_sign = f"{ts}\n{secret}"
    sig = hmac.new(
        secret.encode(), string_to_sign.encode(), hashlib.sha256
    ).digest()
    return quote_plus(base64.b64encode(sig).decode())


# ── Client Protocol ────────────────────────────────────────────


class AlertClient(Protocol):
    async def send(self, msg: AlertMessage) -> None: ...


# ── DingTalk ────────────────────────────────────────────────────


@dataclass
class DingTalkClient:
    webhook: str
    secret: Optional[str] = None
    timeout: float = 5.0
    transport: Optional[httpx.AsyncBaseTransport] = None

    async def send(self, msg: AlertMessage) -> None:
        url = self.webhook
        if self.secret:
            ts = str(int(time.time() * 1000))
            sig = sign_dingtalk(self.secret, ts)
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}timestamp={ts}&sign={sig}"
        payload = {
            "msgtype": "text",
            "text": {"content": msg.to_text()},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as c:
                r = await c.post(url, json=payload)
            if r.status_code >= 400:
                raise AlertError(f"dingtalk HTTP {r.status_code}: {r.text[:200]}")
            body = r.json()
            errcode = body.get("errcode")
            if errcode is not None and errcode != 0:
                raise AlertError(f"dingtalk errcode={errcode}: {body.get('errmsg', '')}")
        except httpx.HTTPError as exc:
            raise AlertError(f"dingtalk 网络错误: {exc}") from exc


# ── Feishu ──────────────────────────────────────────────────────


@dataclass
class FeishuClient:
    webhook: str
    timeout: float = 5.0
    transport: Optional[httpx.AsyncBaseTransport] = None

    async def send(self, msg: AlertMessage) -> None:
        payload = {
            "msg_type": "text",
            "content": {"text": msg.to_text()},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as c:
                r = await c.post(self.webhook, json=payload)
            if r.status_code >= 400:
                raise AlertError(f"feishu HTTP {r.status_code}: {r.text[:200]}")
            body = r.json()
            code = body.get("code")
            if code is not None and code != 0:
                raise AlertError(f"feishu code={code}: {body.get('msg', '')}")
        except httpx.HTTPError as exc:
            raise AlertError(f"feishu 网络错误: {exc}") from exc


# ── Discord ─────────────────────────────────────────────────────


@dataclass
class DiscordClient:
    webhook: str
    timeout: float = 5.0
    transport: Optional[httpx.AsyncBaseTransport] = None

    async def send(self, msg: AlertMessage) -> None:
        payload = {"content": msg.to_text()}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as c:
                r = await c.post(self.webhook, json=payload)
            # Discord 204 No Content = 成功；其它 4xx/5xx = 失败
            if r.status_code >= 400:
                raise AlertError(f"discord HTTP {r.status_code}: {r.text[:200]}")
        except httpx.HTTPError as exc:
            raise AlertError(f"discord 网络错误: {exc}") from exc


# ── 工厂：根据 URL 自动选 client ─────────────────────────────


def detect_client(
    webhook: str,
    *,
    dingtalk_secret: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> AlertClient:
    """根据 URL 域名自动选 client。业务员配 URL 时不用挑类型。"""
    if "dingtalk.com" in webhook:
        return DingTalkClient(webhook=webhook, secret=dingtalk_secret, transport=transport)
    if "feishu.cn" in webhook or "larksuite.com" in webhook:
        return FeishuClient(webhook=webhook, transport=transport)
    if "discord.com" in webhook or "discordapp.com" in webhook:
        return DiscordClient(webhook=webhook, transport=transport)
    raise ValueError(
        f"不支持的 webhook（只识别 dingtalk.com / feishu.cn / discord.com）: {webhook}"
    )
