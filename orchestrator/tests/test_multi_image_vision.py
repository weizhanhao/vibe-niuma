"""Plan 10 Task 9: 多图 vision (≤3 张)。

业务员视角（用户原话）：「不是每次输入都要截图...用户可以多输入几张图」。

设计：
- RawRequest 加 attachments: list[dict]（每条 {mime, b64}），默认 []
- LLMClient.complete_vision_multi(prompt, images: list[(mime, b64)]) 一次塞多图
- BrainstormingSkill 把 raw.attachments 提多张图喂给 vision；单图回落 single screenshot_b64
- 上限 3 张（pydantic schema MAX_ATTACHMENTS_PER_MESSAGE 已经把关）
"""
from __future__ import annotations

import pytest

from orchestrator.adapters.impl.brainstorming_skill import BrainstormingSkill
from orchestrator.adapters.impl._llm import LLMClient
from orchestrator.adapters.types import RawRequest


# ── RawRequest 支持 attachments ────────────────────────────────────


def test_raw_request_attachments_default_empty():
    """attachments 不传时默认空 list（向后兼容老调用）。"""
    raw = RawRequest(
        url="http://x", screenshot_b64="img", box_coords={},
        viewport={}, request_text="?",
    )
    assert raw.attachments == []


def test_raw_request_attachments_accepts_list_of_dicts():
    raw = RawRequest(
        url="http://x", screenshot_b64="", box_coords={},
        viewport={}, request_text="?",
        attachments=[
            {"mime": "image/png", "b64": "AAA"},
            {"mime": "image/jpeg", "b64": "BBB"},
        ],
    )
    assert len(raw.attachments) == 2
    assert raw.attachments[0]["mime"] == "image/png"


def test_raw_request_images_returns_attachments_when_present():
    """images() 拿 attachments 里所有 image/* 项；不含截图也行（attachments 优先）。"""
    raw = RawRequest(
        url="http://x", screenshot_b64="legacy",
        box_coords={}, viewport={}, request_text="?",
        attachments=[
            {"mime": "image/png", "b64": "A"},
            {"mime": "image/jpeg", "b64": "B"},
        ],
    )
    imgs = raw.images()
    assert imgs == [("image/png", "A"), ("image/jpeg", "B")]


def test_raw_request_images_falls_back_to_legacy_screenshot():
    """attachments 空 → images() 拿单 screenshot_b64 兜底（向后兼容）。"""
    raw = RawRequest(
        url="http://x", screenshot_b64="LEG", box_coords={},
        viewport={}, request_text="?",
    )
    imgs = raw.images()
    assert len(imgs) == 1
    assert imgs[0][1] == "LEG"


def test_raw_request_images_filters_non_image_mime():
    """PDF 类附件不进 vision；只挑 image/* 喂模型。"""
    raw = RawRequest(
        url="http://x", screenshot_b64="", box_coords={},
        viewport={}, request_text="?",
        attachments=[
            {"mime": "image/png", "b64": "IMG"},
            {"mime": "application/pdf", "b64": "PDF"},
        ],
    )
    imgs = raw.images()
    assert imgs == [("image/png", "IMG")]


# ── LLMClient.complete_vision_multi ─────────────────────────────


def test_vision_multi_body_has_one_image_block_per_attachment():
    """messages[0].content 应有 1 text + N image_url block。"""
    cli = LLMClient(base_url="http://x", api_key="k", default_model="m")
    images = [
        ("image/png", "AAA"),
        ("image/jpeg", "BBB"),
        ("image/webp", "CCC"),
    ]
    body = cli._vision_multi_body("PROMPT", images, model=None, stream=False)
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "PROMPT"}
    image_blocks = content[1:]
    assert len(image_blocks) == 3
    assert image_blocks[0]["image_url"]["url"] == "data:image/png;base64,AAA"
    assert image_blocks[1]["image_url"]["url"] == "data:image/jpeg;base64,BBB"
    assert image_blocks[2]["image_url"]["url"] == "data:image/webp;base64,CCC"


def test_vision_multi_body_single_image_equivalent_to_complete_vision():
    """单图时 multi 和 single body 等价（content 块一致）。"""
    cli = LLMClient(base_url="http://x", api_key="k", default_model="m")
    body_multi = cli._vision_multi_body("P", [("image/png", "X")], model=None, stream=False)
    body_single = cli._vision_body("P", "X", model=None, mime="image/png", stream=False)
    assert body_multi["messages"][0]["content"][0] == body_single["messages"][0]["content"][0]
    assert body_multi["messages"][0]["content"][1] == body_single["messages"][0]["content"][1]


def test_vision_multi_body_empty_images_only_text_block():
    """没图也能调（纯文本）—— content 只 1 个 text block。"""
    cli = LLMClient(base_url="http://x", api_key="k", default_model="m")
    body = cli._vision_multi_body("P", [], model=None, stream=False)
    content = body["messages"][0]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"


# ── BrainstormingSkill 路由到 multi-vision ──────────────────────────


class _SpyLLM:
    """记录每次 complete_vision* 收到的图数和参数。"""

    def __init__(self, plan_response: str = '{"weight":"light","done":true}'):
        self.plan_response = plan_response
        self.multi_calls: list[list[tuple[str, str]]] = []
        self.single_calls: list[str] = []

    async def complete_vision(self, prompt, image_b64, **kw):
        self.single_calls.append(image_b64)
        return self.plan_response

    async def complete_vision_stream(self, prompt, image_b64, on_token, **kw):
        self.single_calls.append(image_b64)
        return self.plan_response

    async def complete_vision_multi(self, prompt, images, **kw):
        self.multi_calls.append(list(images))
        return self.plan_response

    async def complete_vision_multi_stream(self, prompt, images, on_token, **kw):
        self.multi_calls.append(list(images))
        return self.plan_response


class _NoopChannel:
    async def ask(self, q, options=None): return ""
    async def present_variants(self, v):
        from orchestrator.adapters.types import VariantSelection
        return VariantSelection(selected_id=None)


@pytest.mark.asyncio
async def test_brainstorming_uses_multi_vision_when_multiple_attachments():
    """raw.attachments 含 2+ 张图 → 走 complete_vision_multi（不走 single）。"""
    llm = _SpyLLM()
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    raw = RawRequest(
        url="http://x", screenshot_b64="", box_coords={},
        viewport={}, request_text="?",
        attachments=[
            {"mime": "image/png", "b64": "A"},
            {"mime": "image/png", "b64": "B"},
        ],
    )
    await skill.clarify(raw, _NoopChannel())
    assert len(llm.multi_calls) >= 1
    assert llm.single_calls == []
    assert llm.multi_calls[0] == [("image/png", "A"), ("image/png", "B")]


@pytest.mark.asyncio
async def test_brainstorming_uses_single_vision_for_legacy_one_image():
    """老 raw 只有 screenshot_b64 / 单图 → 维持原 single vision 调用，不走 multi。"""
    llm = _SpyLLM()
    skill = BrainstormingSkill(llm=llm, repo_initializer=None)
    raw = RawRequest(
        url="http://x", screenshot_b64="LEGACY", box_coords={},
        viewport={}, request_text="?",
    )
    await skill.clarify(raw, _NoopChannel())
    assert len(llm.single_calls) >= 1
    assert llm.multi_calls == []
    assert llm.single_calls[0] == "LEGACY"
