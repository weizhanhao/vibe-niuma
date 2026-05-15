from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from orchestrator.db import Base


# 截图 base64 真起来几百 KB，TEXT (MySQL 65535 字节上限) 撑爆。
# 同样 fail_log 拖着真 traceback 也可能超。with_variant 让 sqlite 测试还走 Text。
_BIG_TEXT = Text().with_variant(LONGTEXT, "mysql")


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    screenshot_b64: Mapped[str] = mapped_column(_BIG_TEXT, nullable=False)
    box_coords: Mapped[dict] = mapped_column(JSON, nullable=False)
    viewport: Mapped[dict] = mapped_column(JSON, nullable=False)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    fail_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fail_log: Mapped[str | None] = mapped_column(_BIG_TEXT, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preview_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    preview_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
