"""Plan 10 Task 2: Attachment + MessageOut pydantic 测试。

业务员视角：
  - 一条 message 可能带 0-3 张附件（截图 / 框选 / 贴图 / PDF）
  - 框选才有 box 坐标，其他附件 box 留空
  - 多附件全部走 attachments[]；老 single screenshot_b64 字段兜底转一个 attachment
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.schemas import (
    Attachment,
    CreateChangeRequestIn,
    MessageOut,
)


# ── Attachment 单体校验 ─────────────────────────────────────────────


def test_attachment_framed_region_with_box_ok():
    a = Attachment(
        kind="framed_region",
        mime="image/png",
        b64="iVBOR0K...",
        url="http://example.com/page",
        box={"x": 10, "y": 20, "width": 100, "height": 80},
        viewport={"width": 1280, "height": 720},
    )
    assert a.kind == "framed_region"
    assert a.box["width"] == 100


def test_attachment_pasted_image_no_box_ok():
    a = Attachment(
        kind="pasted_image",
        mime="image/jpeg",
        b64="/9j/4AAQ...",
    )
    assert a.kind == "pasted_image"
    assert a.box is None


def test_attachment_screenshot_active_tab_ok():
    a = Attachment(
        kind="screenshot_active_tab",
        mime="image/png",
        b64="iVBOR0K...",
        url="http://example.com/",
        viewport={"width": 1024, "height": 768},
    )
    assert a.kind == "screenshot_active_tab"


def test_attachment_attached_file_pdf_ok():
    a = Attachment(
        kind="attached_file",
        mime="application/pdf",
        b64="JVBERi0...",
        name="spec.pdf",
    )
    assert a.kind == "attached_file"
    assert a.name == "spec.pdf"


def test_attachment_kind_unknown_rejected():
    with pytest.raises(ValidationError):
        Attachment(kind="invalid_kind", mime="image/png", b64="x")


def test_attachment_missing_mime_rejected():
    with pytest.raises(ValidationError):
        Attachment(kind="pasted_image", b64="x")  # type: ignore[call-arg]


# ── CreateChangeRequestIn 兼容老 single screenshot 和新 attachments[] ──


def test_create_change_request_accepts_attachments_list():
    payload = CreateChangeRequestIn(
        url="http://x/orders",
        request_text="改红",
        attachments=[
            {
                "kind": "framed_region",
                "mime": "image/png",
                "b64": "img1",
                "box": {"x": 0, "y": 0, "width": 10, "height": 10},
                "viewport": {"width": 1024, "height": 768},
            },
            {
                "kind": "pasted_image",
                "mime": "image/jpeg",
                "b64": "img2",
            },
        ],
    )
    assert len(payload.attachments) == 2  # type: ignore[arg-type]
    assert payload.attachments[0].kind == "framed_region"  # type: ignore[index]


def test_create_change_request_legacy_single_screenshot_still_works():
    """v0.5 客户端传单 screenshot_b64：兜底转一个 framed_region attachment。"""
    payload = CreateChangeRequestIn(
        url="http://x/orders",
        screenshot_b64="oldimg",
        box_coords={"x": 1, "y": 2, "width": 3, "height": 4},
        viewport={"width": 1024, "height": 768},
        request_text="改红",
    )
    assert payload.screenshot_b64 == "oldimg"
    atts = payload.normalize_attachments()
    assert len(atts) == 1
    assert atts[0].kind == "framed_region"
    assert atts[0].b64 == "oldimg"
    assert atts[0].box == {"x": 1, "y": 2, "width": 3, "height": 4}


def test_create_change_request_attachments_take_priority_over_legacy_screenshot():
    """attachments 显式给了就用它，screenshot_b64 字段忽略。"""
    payload = CreateChangeRequestIn(
        url="http://x/orders",
        screenshot_b64="oldimg",
        box_coords={},
        viewport={},
        request_text="改红",
        attachments=[
            {"kind": "pasted_image", "mime": "image/png", "b64": "newimg"},
        ],
    )
    atts = payload.normalize_attachments()
    assert len(atts) == 1
    assert atts[0].b64 == "newimg"


def test_create_change_request_attachments_max_3_enforced():
    """业务员一次最多贴 3 张图；超出 ValidationError。"""
    with pytest.raises(ValidationError):
        CreateChangeRequestIn(
            url="http://x",
            request_text="x",
            attachments=[
                {"kind": "pasted_image", "mime": "image/png", "b64": "1"},
                {"kind": "pasted_image", "mime": "image/png", "b64": "2"},
                {"kind": "pasted_image", "mime": "image/png", "b64": "3"},
                {"kind": "pasted_image", "mime": "image/png", "b64": "4"},
            ],
        )


# ── MessageOut shape ────────────────────────────────────────────────


def test_message_out_user_with_attachments_serializable():
    m = MessageOut(
        id="msg_0001",
        ts="2026-05-17T10:00:00",
        type="user",
        content="改红",
        attachments=[
            Attachment(kind="pasted_image", mime="image/png", b64="x"),
        ],
        cr_id="cr_abc",
        cr_mode="new_cr",
    )
    assert m.type == "user"
    assert len(m.attachments or []) == 1
    assert m.cr_mode == "new_cr"


def test_message_out_ai_no_attachments():
    m = MessageOut(
        id="msg_0002",
        ts="2026-05-17T10:00:05",
        type="ai",
        content="改完了，看预览",
    )
    assert m.type == "ai"
    assert m.attachments is None


def test_message_out_summary_with_replaces_meta():
    m = MessageOut(
        id="msg_0003",
        ts="2026-05-17T10:00:10",
        type="summary",
        content="历史摘要",
        replaces_count=47,
        replaces_token_estimate=12345,
    )
    assert m.replaces_count == 47


def test_message_out_unknown_type_rejected():
    with pytest.raises(ValidationError):
        MessageOut(
            id="msg_0004",
            ts="2026-05-17T10:00:00",
            type="bogus_type",  # type: ignore[arg-type]
            content="x",
        )
