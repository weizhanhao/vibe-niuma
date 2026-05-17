"""LLMClient —— 给 BrainstormingSkill 用的薄 LLM 客户端。

走 OpenAI-compatible 的 Chat Completions 接口（LiteLLM / one-api 这类代理基本都
走这个），所以单一接口就能覆盖 DeepSeek / 通义 / Claude（Anthropic-compatible
proxy 把请求转给真实 provider）。

只暴露两个能力：纯文本补全 / 图文（vision）补全。YAGNI：流式、function call、
工具等都不需要。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from orchestrator.config import settings

OnTokenCallback = Callable[[str], Awaitable[None]]


def _detect_image_mime(b64: str) -> str:
    """按 base64 头几个字节的魔数判 PNG/JPEG。识别不出退 PNG（最常见）。"""
    if not b64:
        return "image/png"
    if b64.startswith("/9j/"):       # JPEG: 二进制 \xff\xd8
        return "image/jpeg"
    if b64.startswith("iVBOR"):      # PNG: 二进制 \x89PNG
        return "image/png"
    if b64.startswith("R0lGOD"):     # GIF
        return "image/gif"
    if b64.startswith("UklGR"):      # WebP
        return "image/webp"
    return "image/png"


@dataclass
class LLMClient:
    """实例化时锁定 base URL / api key / 默认模型。"""

    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    timeout: float = 60.0

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or settings.anthropic_base_url).rstrip("/")
        self.api_key = self.api_key or settings.llm_api_key
        self.default_model = self.default_model or settings.dev_model

    async def complete(self, prompt: str, *, model: str | None = None) -> str:
        body = {
            "model": model or self.default_model,
            "messages": [{"role": "user", "content": prompt}],
        }
        return await self._post_chat(body)

    async def complete_vision(
        self, prompt: str, image_b64: str, *, model: str | None = None,
        mime: str | None = None,
    ) -> str:
        # mime 不指定时按 base64 头部魔数自动判：JPEG 以 /9j/ 起，PNG 以 iVBOR 起。
        # 扩展为减小上传体积已把截图换 JPEG，这里要识别正确否则 qwen-vl-plus 不认。
        body = self._vision_body(prompt, image_b64, model=model, mime=mime, stream=False)
        return await self._post_chat(body)

    async def complete_vision_stream(
        self, prompt: str, image_b64: str, on_token: OnTokenCallback,
        *, model: str | None = None, mime: str | None = None,
    ) -> str:
        """流式视觉补全：每个 token chunk 回调 on_token；返回累计全文。

        失败/服务器不支持 stream 时 on_token 不会被回调，但会抛 RuntimeError，
        调用方应捕获并降级到 complete_vision（非流式）。
        """
        body = self._vision_body(prompt, image_b64, model=model, mime=mime, stream=True)
        return await self._stream_chat(body, on_token)

    # Plan 10 Task 9：多图 vision —— 一次塞 ≤3 张图。pydantic schema 那层
    # MAX_ATTACHMENTS_PER_MESSAGE=3 是「业务上限」；这里不二次限制，让客户端
    # 自己决定怎么处理（误传 100 张 vision API 会自己拒）。
    async def complete_vision_multi(
        self, prompt: str, images: list[tuple[str, str]], *,
        model: str | None = None,
    ) -> str:
        body = self._vision_multi_body(prompt, images, model=model, stream=False)
        return await self._post_chat(body)

    async def complete_vision_multi_stream(
        self, prompt: str, images: list[tuple[str, str]],
        on_token: OnTokenCallback, *, model: str | None = None,
    ) -> str:
        body = self._vision_multi_body(prompt, images, model=model, stream=True)
        return await self._stream_chat(body, on_token)

    def _vision_multi_body(
        self, prompt: str, images: list[tuple[str, str]], *,
        model: str | None, stream: bool,
    ) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for mime, b64 in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        body: dict = {
            "model": model or settings.vision_model,
            "messages": [{"role": "user", "content": content}],
        }
        if stream:
            body["stream"] = True
        return body

    def _vision_body(
        self, prompt: str, image_b64: str, *, model: str | None,
        mime: str | None, stream: bool,
    ) -> dict:
        resolved_mime = mime or _detect_image_mime(image_b64)
        body: dict = {
            "model": model or settings.vision_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{resolved_mime};base64,{image_b64}"},
                    },
                ],
            }],
        }
        if stream:
            body["stream"] = True
        return body

    async def _post_chat(self, body: dict) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            resp = await cli.post(url, headers=headers, content=json.dumps(body))
        if resp.status_code >= 400:
            raise RuntimeError(
                f"LLM 上游 HTTP {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM 响应格式异常: {data}") from exc

    async def _stream_chat(self, body: dict, on_token: OnTokenCallback) -> str:
        """OpenAI 兼容 SSE 流：解析 `data: {...}\\n\\n` chunk，取 delta.content。

        累计返回完整文本；on_token 回调每个 delta（已 strip 空白）。
        服务器返回 `data: [DONE]` 时正常结束。HTTP >=400 → RuntimeError。
        """
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        full: list[str] = []
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            async with cli.stream(
                "POST", url, headers=headers, content=json.dumps(body),
            ) as resp:
                if resp.status_code >= 400:
                    err = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"LLM 流式上游 HTTP {resp.status_code}: {err[:500]}"
                    )
                async for raw_line in resp.aiter_lines():
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    payload = raw_line[5:].strip()
                    if not payload or payload == "[DONE]":
                        if payload == "[DONE]":
                            break
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    try:
                        delta = chunk["choices"][0].get("delta", {}) or {}
                        text = delta.get("content") or ""
                    except (KeyError, IndexError, TypeError):
                        text = ""
                    if not text:
                        continue
                    full.append(text)
                    try:
                        await on_token(text)
                    except Exception:
                        pass
        return "".join(full)
