"""Pydantic schemas —— REST 边界数据契约。

Plan 10 起：
- Attachment：业务员一次 message 可带 0-3 张附件（截图 / 框选 / 贴图 / PDF）
- MessageOut：conversation.messages JSON list 里每条 message 的形状
- CreateChangeRequestIn 兼容老 single screenshot_b64 字段（v0.5 客户端还在用）
  + 新加 attachments[] 字段；调用方用 normalize_attachments() 拿统一视图
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


AttachmentKind = Literal[
    "framed_region",          # 业务员拖框出的精确区域 + box 坐标
    "screenshot_active_tab",  # 自动截当前 tab，全屏，box 留空
    "pasted_image",           # 业务员从剪贴板 / 文件选择器贴的图
    "attached_file",          # PDF 等非图文件
    "annotated_screenshot",   # AnnotateOverlay 烘焙的 PNG（含红框/箭头/文字/马赛克）
]


class Attachment(BaseModel):
    """一条 user message 携带的单个附件。

    所有附件都带 mime + b64（base64 内容）。截图类型可选 url（业务员当前页）
    和 viewport（按 viewport 比例渲染 box）。
    """
    kind: AttachmentKind
    mime: str
    b64: str
    url: str | None = None
    box: dict[str, int] | None = None
    viewport: dict[str, int] | None = None
    name: str | None = None


# Plan 10 spec §架构：附件上限 3 张（vision API token 安全区）
MAX_ATTACHMENTS_PER_MESSAGE = 3


class CreateChangeRequestIn(BaseModel):
    """POST /change-requests 入参。

    兼容性：v0.5 客户端用 screenshot_b64 + box_coords + viewport 三字段（单图）；
    Plan 10 新客户端用 attachments[]（多图）。两套并存，调用方用
    normalize_attachments() 拿统一的 Attachment[] 视图。
    """
    url: str = ""
    # 老字段（v0.5 单截图模式）—— 保留兼容
    screenshot_b64: str = ""
    box_coords: dict = Field(default_factory=dict)
    viewport: dict = Field(default_factory=dict)
    request_text: str
    # Plan 9：可选挂到现有 conversation；缺省则 orchestrator 自动 create 一个
    conversation_id: str | None = None
    # Plan 10：多附件模式（≤3 张）
    attachments: list[Attachment] | None = None

    @field_validator("attachments")
    @classmethod
    def _max_3(cls, v: list[Attachment] | None) -> list[Attachment] | None:
        if v is not None and len(v) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise ValueError(
                f"附件最多 {MAX_ATTACHMENTS_PER_MESSAGE} 张，给了 {len(v)} 张"
            )
        return v

    def normalize_attachments(self) -> list[Attachment]:
        """拿统一的 Attachment[] 视图 —— attachments 优先；老 single screenshot_b64
        作 framed_region 兜底。"""
        if self.attachments:
            return self.attachments
        if self.screenshot_b64:
            return [Attachment(
                kind="framed_region",
                mime="image/png",
                b64=self.screenshot_b64,
                url=self.url or None,
                box=self.box_coords or None,
                viewport=self.viewport or None,
            )]
        return []


class AnswerIn(BaseModel):
    question_id: str
    answer: str


class PostMessageIn(BaseModel):
    """POST /conversations/{conv_id}/messages 入参。

    Plan 10 Task 10：业务员一次输入（chat 或截图改）的统一入口。
    intent classifier 根据 text + attachments + 历史决定路由到 new_cr/refine_cr/chat_only。
    override_mode：业务员显式强制（⇧⌘↵=force new_cr），跳过 LLM 分类。
    """
    text: str
    attachments: list[Attachment] | None = None
    override_mode: Literal["new_cr", "refine_cr", "chat_only"] | None = None

    @field_validator("attachments")
    @classmethod
    def _max_3(cls, v: list[Attachment] | None) -> list[Attachment] | None:
        if v is not None and len(v) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise ValueError(
                f"附件最多 {MAX_ATTACHMENTS_PER_MESSAGE} 张，给了 {len(v)} 张"
            )
        return v


class PostMessageOut(BaseModel):
    """POST /conversations/{conv_id}/messages 出参。

    - mode='chat_only' → cr_id=None, ai_message_id 是新 AI message 的 id
    - mode='new_cr' / 'refine_cr' → cr_id 是新 CR 的 id, ai_message_id=None
    - is_unsure：classifier 信心 <0.6，UI 提示业务员「不对点这里改 mode」
    """
    message_id: str
    mode: Literal["new_cr", "refine_cr", "chat_only"]
    cr_id: str | None = None
    ai_message_id: str | None = None
    confidence: float
    is_unsure: bool
    reason: str = ""


MessageType = Literal["user", "ai", "summary", "system"]


class MessageOut(BaseModel):
    """conversation.messages JSON 里每条 message 的 shape。

    user message 可能带 attachments + 关联触发的 cr_id + cr_mode。
    ai / summary / system 一般只 content。summary 带 replaces_* 元信息。
    """
    id: str
    ts: str
    type: MessageType
    content: str
    attachments: list[Attachment] | None = None
    cr_id: str | None = None
    cr_mode: Literal["new_cr", "refine_cr"] | None = None
    replaces_count: int | None = None
    replaces_token_estimate: int | None = None
    meta: dict[str, Any] | None = None


class ChangeRequestOut(BaseModel):
    id: str
    state: str
    url: str
    request_text: str
    branch: str | None
    preview_url: str | None
    fail_phase: str | None
    fail_reason: str | None
    retry_of: str | None
    conversation_id: str | None = None
    repos: dict | None = None
    # Plan 10 字段
    mode: str | None = None
    refine_of: str | None = None
    self_heal_attempts: int = 0

    @classmethod
    def from_model(cls, cr) -> "ChangeRequestOut":
        return cls(
            id=cr.id,
            state=cr.state,
            url=cr.url,
            request_text=cr.request_text,
            branch=cr.branch,
            preview_url=cr.preview_url,
            fail_phase=cr.fail_phase,
            fail_reason=cr.fail_reason,
            retry_of=cr.retry_of,
            conversation_id=getattr(cr, "conversation_id", None),
            repos=getattr(cr, "repos", None),
            mode=getattr(cr, "mode", None),
            refine_of=getattr(cr, "refine_of", None),
            self_heal_attempts=getattr(cr, "self_heal_attempts", 0) or 0,
        )
