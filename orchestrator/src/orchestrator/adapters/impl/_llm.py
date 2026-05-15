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

import httpx

from orchestrator.config import settings


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
        resolved_mime = mime or _detect_image_mime(image_b64)
        body = {
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
        return await self._post_chat(body)

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
