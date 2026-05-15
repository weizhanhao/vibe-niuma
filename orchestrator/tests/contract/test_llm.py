"""LLMClient 契约测试 —— 不打真实模型，mock httpx 验证请求形状。"""
from __future__ import annotations

import json

import httpx
import pytest

from orchestrator.adapters.impl._llm import LLMClient


def _mock_transport(captured: dict, *, status: int = 200, body: dict | None = None):
    body = body or {"choices": [{"message": {"content": "hello"}}]}
    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(status, json=body)
    return httpx.MockTransport(_handler)


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch):
    """替换 httpx.AsyncClient 的 transport，让所有请求走 MockTransport。"""
    holder = {"transport": _mock_transport({})}
    real_init = httpx.AsyncClient.__init__
    def _init(self, *a, **kw):
        kw["transport"] = holder["transport"]
        real_init(self, *a, **kw)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return holder


async def test_complete_text_posts_to_base_url(_patch_async_client):
    captured: dict = {}
    _patch_async_client["transport"] = _mock_transport(captured)
    client = LLMClient(base_url="http://proxy:8787", api_key="sk-x", default_model="m1")
    result = await client.complete("hi")
    assert result == "hello"
    assert captured["url"] == "http://proxy:8787/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-x"
    assert captured["body"]["model"] == "m1"
    assert captured["body"]["messages"][0]["content"] == "hi"


async def test_complete_vision_includes_image(_patch_async_client):
    captured: dict = {}
    _patch_async_client["transport"] = _mock_transport(captured)
    client = LLMClient(base_url="http://proxy:8787", api_key="k")
    await client.complete_vision("看截图", image_b64="aGk=", model="vis-1")
    body = captured["body"]
    assert body["model"] == "vis-1"
    content = body["messages"][0]["content"]
    assert isinstance(content, list)
    types = [c["type"] for c in content]
    assert types == ["text", "image_url"]
    assert "data:image/png;base64,aGk=" in content[1]["image_url"]["url"]


async def test_complete_raises_on_http_error(_patch_async_client):
    _patch_async_client["transport"] = _mock_transport({}, status=500, body={"error": "boom"})
    client = LLMClient(base_url="http://proxy:8787", api_key="k")
    with pytest.raises(RuntimeError, match="LLM 上游 HTTP 500"):
        await client.complete("hi")


async def test_complete_raises_on_malformed_response(_patch_async_client):
    _patch_async_client["transport"] = _mock_transport({}, body={"unexpected": True})
    client = LLMClient(base_url="http://proxy:8787", api_key="k")
    with pytest.raises(RuntimeError, match="响应格式异常"):
        await client.complete("hi")


def _sse_transport(captured: dict, chunks: list[str], *, status: int = 200):
    """造一个 OpenAI 兼容 SSE 流。每个 chunk 是 delta.content 文本。"""
    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        lines: list[str] = []
        for c in chunks:
            payload = json.dumps({"choices": [{"delta": {"content": c}}]})
            lines.append(f"data: {payload}\n\n")
        lines.append("data: [DONE]\n\n")
        body = "".join(lines).encode()
        return httpx.Response(
            status, content=body,
            headers={"content-type": "text/event-stream"},
        )
    return httpx.MockTransport(_handler)


async def test_complete_vision_stream_emits_each_chunk(_patch_async_client):
    captured: dict = {}
    _patch_async_client["transport"] = _sse_transport(captured, ["he", "llo", " world"])
    client = LLMClient(base_url="http://proxy:8787", api_key="k")
    received: list[str] = []

    async def _on_tok(tok: str) -> None:
        received.append(tok)

    text = await client.complete_vision_stream("prompt", "aGk=", on_token=_on_tok)
    assert text == "hello world"
    assert received == ["he", "llo", " world"]
    assert captured["body"]["stream"] is True


async def test_complete_vision_stream_raises_on_http_error(_patch_async_client):
    _patch_async_client["transport"] = _sse_transport({}, [], status=500)
    client = LLMClient(base_url="http://proxy:8787", api_key="k")

    async def _on_tok(_: str) -> None:
        return None

    with pytest.raises(RuntimeError, match="流式上游 HTTP 500"):
        await client.complete_vision_stream("p", "aGk=", on_token=_on_tok)
