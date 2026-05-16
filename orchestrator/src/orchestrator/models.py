from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
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
        String(512), default="/opt/doskill/demo", nullable=False
    )
    preview_backend_url: Mapped[str] = mapped_column(
        String(512), default="http://doskill-demo-backend:8000", nullable=False
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
