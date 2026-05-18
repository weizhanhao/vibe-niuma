from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, Index
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from orchestrator.db import Base


# 截图 base64 真起来几百 KB，TEXT (MySQL 65535 字节上限) 撑爆。
# 同样 fail_log 拖着真 traceback 也可能超。with_variant 让 sqlite 测试还走 Text。
_BIG_TEXT = Text().with_variant(LONGTEXT, "mysql")


class SystemConfig(Base):
    """Plan 6 —— 系统配置 singleton（id 永远 = 1）。

    内容对应 Plan 6 schema 的 `server.*` 部分：dev_runner / dev_model /
    vision_model / *_api_key / demo_repo_path / preview_backend_url。

    生命周期：
      - lifespan 启动时 bootstrap 自 `.env`（Task 3 写）；
      - `/admin/config` PUT 后 DB 主导，并 invalidate `get_settings()` 缓存（Task 3）；
      - `version` 单调递增，PUT 用乐观锁，stale → 409。

    数据库迁移说明：
      `Base.metadata.create_all()` 只新建表，不 ALTER 已存在表的列。
      老库新加字段需要手动 `ALTER TABLE system_config ADD COLUMN ...`，
      或者 drop 后重建（singleton 数据从 .env bootstrap 就回得来）。
    """
    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    dev_runner: Mapped[str] = mapped_column(String(32), default="opencode", nullable=False)
    dev_model: Mapped[str] = mapped_column(
        String(128), default="deepseek/deepseek-v4-flash", nullable=False
    )
    vision_model: Mapped[str] = mapped_column(
        String(128), default="qwen-vl-plus", nullable=False
    )
    deepseek_api_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dashscope_api_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    anthropic_api_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    demo_repo_path: Mapped[str] = mapped_column(
        String(512), default="/opt/vibe-niuma/demo", nullable=False
    )
    preview_backend_url: Mapped[str] = mapped_column(
        String(512), default="http://vibe-niuma-demo-backend:8000", nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


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
    # Plan 8 Task 11：多仓项目时记录这个 CR 触达了哪些子仓 + 各自 commit SHA。
    # 单仓 CR 留 None；多仓时存 {"frontend": "abc123", "backend": "def456"}。
    repos: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Plan 9 Task 1：N 个 CR 串成一个 conversation；老 CR 默认 NULL，迁移到 Legacy bucket
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("conversation.id"), nullable=True, index=True,
    )
    # Plan 10：多附件（图 / PDF）的 JSON 数组；老 single screenshot_b64 字段保留兼容
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Plan 10：CR 走的路径模式；None 表示老 CR（一律视为 new_cr）。
    # 'new_cr' = 完整 pipeline；'refine_cr' = 续上一 CR branch
    # （'chat_only' 不产生 CR 不会写到这里）
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Plan 10：refine_cr 关联的上一 CR id；non-refine 留 None
    refine_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Plan 10 self_heal：CR 失败后 AI 自动 retry 的次数（≤ MAX_SELF_HEAL_ATTEMPTS=2）
    self_heal_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )


# Plan 9：N 个 CR 串成一个对话容器；chat 上下文跨 session 持久化在此。
# messages 是 append-only JSON 数组，item 形如：
#   {"type": "user" | "ai" | "summary", "ts": "ISO", "content": "...", ...}
class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # append-only：[{type, ts, content, ...}]
    messages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
