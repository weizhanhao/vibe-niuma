"""Plan 11 M3.T21 —— alert.py 单测：钉钉 + 飞书 + Discord webhook + 签名。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest

from orchestrator.alert import (
    AlertError,
    AlertMessage,
    DingTalkClient,
    DiscordClient,
    FeishuClient,
    detect_client,
    sign_dingtalk,
)


# ── 签名 ────────────────────────────────────────────────────────


def test_sign_dingtalk_matches_official_formula():
    """钉钉签名公式：HMAC-SHA256(secret, ts + '\\n' + secret) → base64 → url-encode。"""
    secret = "SECxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    ts = "1717000000000"

    sig = sign_dingtalk(secret, ts)

    string_to_sign = f"{ts}\n{secret}"
    expected_b64 = base64.b64encode(
        hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    assert unquote(sig) == expected_b64


def test_sign_dingtalk_handles_special_chars_in_base64():
    """base64 含 +/= 时 url-quote 应替成 %2B/%2F/%3D。"""
    sig = sign_dingtalk("k+/=", "1234")
    assert "+" not in sig
    assert "/" not in sig


# ── DingTalk ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dingtalk_send_text_posts_correct_shape():
    captured: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append({
            "url": str(req.url),
            "body": json.loads(req.content.decode()),
        })
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    client = DingTalkClient(
        webhook="https://oapi.dingtalk.com/robot/send?access_token=TOKEN",
        secret="SECxxx",
        transport=httpx.MockTransport(handler),
    )
    await client.send(AlertMessage(
        title="vibe-niuma 异常",
        body="mysql 挂了",
        link_url="http://x/cr/1",
    ))
    assert len(captured) == 1
    parsed = urlparse(captured[0]["url"])
    qs = parse_qs(parsed.query)
    assert "access_token" in qs
    assert "timestamp" in qs
    assert "sign" in qs
    body = captured[0]["body"]
    assert body["msgtype"] == "text"
    assert "vibe-niuma 异常" in body["text"]["content"]
    assert "mysql 挂了" in body["text"]["content"]


@pytest.mark.asyncio
async def test_dingtalk_send_no_secret_skips_sign():
    captured: list[str] = []

    def handler(req):
        captured.append(str(req.url))
        return httpx.Response(200, json={"errcode": 0})

    client = DingTalkClient(
        webhook="https://oapi.dingtalk.com/robot/send?access_token=T",
        secret=None,
        transport=httpx.MockTransport(handler),
    )
    await client.send(AlertMessage(title="x", body="y"))
    assert "sign=" not in captured[0]


@pytest.mark.asyncio
async def test_dingtalk_errcode_nonzero_raises_alert_error():
    def handler(req):
        return httpx.Response(200, json={"errcode": 310000, "errmsg": "keywords not in content"})

    client = DingTalkClient(
        webhook="https://oapi.dingtalk.com/robot/send?access_token=T",
        secret=None,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AlertError, match="keywords not in content"):
        await client.send(AlertMessage(title="x", body="y"))


# ── Feishu ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_feishu_send_uses_post_text_format():
    captured: list[dict] = []

    def handler(req):
        captured.append({"url": str(req.url), "body": json.loads(req.content.decode())})
        return httpx.Response(200, json={"code": 0, "msg": "ok"})

    client = FeishuClient(
        webhook="https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        transport=httpx.MockTransport(handler),
    )
    await client.send(AlertMessage(title="title", body="body"))
    assert len(captured) == 1
    assert captured[0]["body"]["msg_type"] == "text"
    assert "title" in captured[0]["body"]["content"]["text"]
    assert "body" in captured[0]["body"]["content"]["text"]


@pytest.mark.asyncio
async def test_feishu_code_nonzero_raises():
    def handler(req):
        return httpx.Response(200, json={"code": 19021, "msg": "sign error"})

    client = FeishuClient(
        webhook="https://open.feishu.cn/x",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AlertError, match="sign error"):
        await client.send(AlertMessage(title="x", body="y"))


# ── Discord ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discord_send_uses_content_field():
    captured: list[dict] = []

    def handler(req):
        captured.append(json.loads(req.content.decode()))
        return httpx.Response(204)

    client = DiscordClient(
        webhook="https://discord.com/api/webhooks/xxx/yyy",
        transport=httpx.MockTransport(handler),
    )
    await client.send(AlertMessage(title="ALERT", body="mysql down"))
    assert "ALERT" in captured[0]["content"]
    assert "mysql down" in captured[0]["content"]


@pytest.mark.asyncio
async def test_discord_4xx_raises():
    def handler(req):
        return httpx.Response(404, text="not found")

    client = DiscordClient(
        webhook="https://discord.com/x",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AlertError, match="HTTP 404"):
        await client.send(AlertMessage(title="x", body="y"))


# ── detect_client ───────────────────────────────────────────────


def test_detect_client_dingtalk():
    c = detect_client("https://oapi.dingtalk.com/robot/send?access_token=xxx")
    assert isinstance(c, DingTalkClient)


def test_detect_client_feishu():
    c = detect_client("https://open.feishu.cn/open-apis/bot/v2/hook/xxx")
    assert isinstance(c, FeishuClient)


def test_detect_client_discord():
    c = detect_client("https://discord.com/api/webhooks/123/abc")
    assert isinstance(c, DiscordClient)


def test_detect_client_unknown_raises():
    with pytest.raises(ValueError, match="不支持的 webhook"):
        detect_client("https://example.com/whatever")
