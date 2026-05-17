"""Conversation 仓库 —— Plan 9 持久对话容器的 CRUD。

设计要点（spec §数据契约）：
- Conversation = N 个 ChangeRequest 的容器；chat history 在 conversation.messages JSON
- messages append-only：写入永远是 list 末尾追加，永不删
- 软删用 archived_at；硬删由 reaper job 跑 3 天后
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.models import Conversation


def _new_id() -> str:
    """ulid-ish：32 字符短 id。"""
    return secrets.token_hex(16)


class ConversationRepository:
    def __init__(self, db: Session):
        self._db = db

    def create(self, title: str = "") -> Conversation:
        conv = Conversation(id=_new_id(), title=title, messages=[])
        self._db.add(conv)
        self._db.commit()
        self._db.refresh(conv)
        return conv

    def get(self, conv_id: str) -> Conversation | None:
        return self._db.get(Conversation, conv_id)

    def list_active(self) -> list[Conversation]:
        stmt = select(Conversation).where(Conversation.archived_at.is_(None)).order_by(
            Conversation.updated_at.desc(),
        )
        return list(self._db.scalars(stmt))

    def list_all(self) -> list[Conversation]:
        stmt = select(Conversation).order_by(Conversation.updated_at.desc())
        return list(self._db.scalars(stmt))

    def list_meta(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """列出对话元数据 —— **跳过 messages JSON 列**。

        messages 是 JSON 列，单条 conversation 累积 LLM 历史后可能上 MB。
        ORDER BY updated_at DESC 时 MySQL 必须把整行加进 sort buffer，
        几条大行就撞 sort_buffer_size 限制（1038 Out of sort memory）。
        list 接口不需要 messages，逐 conv GET 时再取。
        """
        from sqlalchemy import select as sa_select
        cols = (
            Conversation.id, Conversation.title,
            Conversation.created_at, Conversation.updated_at,
            Conversation.archived_at,
        )
        stmt = sa_select(*cols)
        if not include_archived:
            stmt = stmt.where(Conversation.archived_at.is_(None))
        stmt = stmt.order_by(Conversation.updated_at.desc())
        rows = self._db.execute(stmt).all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "archived_at": r.archived_at.isoformat() if r.archived_at else None,
                # 兼容老客户端：messages 字段保留但置空（按需 GET 单条对话取详情）
                "messages": [],
            }
            for r in rows
        ]

    def append_message(self, conv_id: str, message: dict[str, Any]) -> Conversation:
        """append 一条 message；自动更新 updated_at + title（首句 user 自动当 title）。"""
        conv = self._db.get(Conversation, conv_id)
        if conv is None:
            raise KeyError(f"Conversation not found: {conv_id}")
        # 复制以触发 SQLAlchemy 检测 JSON mutation
        msgs = list(conv.messages or [])
        msgs.append(message)
        conv.messages = msgs
        # 首句 user 自动当 title
        if not conv.title and message.get("type") == "user":
            content = str(message.get("content", "")).strip()
            conv.title = (content[:50] + "…") if len(content) > 50 else content or "（未命名对话）"
        self._db.commit()
        self._db.refresh(conv)
        return conv

    def archive(self, conv_id: str) -> Conversation:
        conv = self._db.get(Conversation, conv_id)
        if conv is None:
            raise KeyError(f"Conversation not found: {conv_id}")
        conv.archived_at = datetime.utcnow()
        self._db.commit()
        self._db.refresh(conv)
        return conv
