"""Plan 6 Task 1 —— SystemConfig singleton 仓储。

ORM 模型本身放在 `orchestrator.models.SystemConfig`，跟 `ChangeRequest`
一个注册中心，conftest.py 一处 import 就把表也建出来。这里只放仓储
（CRUD + 乐观锁）和相关异常。
"""
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from orchestrator.models import SystemConfig


class StaleVersionError(Exception):
    """乐观锁失败：客户端拿着旧 version 提交 → 拒绝。

    Plan 6 /admin/config PUT 把这个映射成 HTTP 409。
    """


class SystemConfigRepository:
    """singleton 仓储 —— 全表只有 id=1 一行。

    - `get_or_create()` 第一次调用建默认行；之后幂等返回。
    - `update(patch, expected_version)` 乐观锁：version 不匹配抛 StaleVersionError，
      匹配则用 patch dict 增量更新（None 也算合法值），version += 1，updated_at = now。
    """

    SINGLETON_ID = 1

    def __init__(self, db: Session):
        self._db = db

    def get_or_create(self) -> SystemConfig:
        cfg = self._db.get(SystemConfig, self.SINGLETON_ID)
        if cfg is not None:
            return cfg

        # 第一次启动：用 ORM 字段定义的 default 值建一行。
        # 显式给 id 防止 MySQL autoincrement 给个 != 1 的值。
        cfg = SystemConfig(id=self.SINGLETON_ID)
        self._db.add(cfg)
        self._db.commit()
        self._db.refresh(cfg)
        return cfg

    def update(self, patch: dict[str, Any], expected_version: int) -> SystemConfig:
        cfg = self.get_or_create()
        if cfg.version != expected_version:
            raise StaleVersionError(
                f"expected version {expected_version} but current is {cfg.version}; "
                f"重新 GET /admin/config 后再 PUT"
            )

        # 增量更新；只动 patch 里列出来的字段。未知字段直接拒（防扩展端拼错列名）。
        allowed = {
            "dev_runner",
            "dev_model",
            "vision_model",
            "deepseek_api_key",
            "dashscope_api_key",
            "anthropic_api_key",
            "demo_repo_path",
            "preview_backend_url",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"unknown system_config fields: {sorted(unknown)}")

        for key, value in patch.items():
            setattr(cfg, key, value)
        cfg.version = expected_version + 1
        cfg.updated_at = datetime.utcnow()

        self._db.commit()
        self._db.refresh(cfg)
        return cfg
