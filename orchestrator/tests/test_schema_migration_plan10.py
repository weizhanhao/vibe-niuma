"""Plan 10 Task 1: schema 自适应 migration 测试。

ECS 没 alembic，靠 lifespan _ensure_schema_migrations 幂等加列。
Plan 10 加 4 列：attachments / mode / refine_of / self_heal_attempts。

每个测试：
  1. drop 那一列模拟「ECS 是老 schema」
  2. 跑 _ensure_schema_migrations
  3. 用 information_schema 查列是否补上
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from orchestrator.main import _ensure_schema_migrations


def _column_exists(engine, table: str, column: str) -> bool:
    if engine.dialect.name == "mysql":
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
            ), {"t": table, "c": column}).scalar() is not None
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def test_ensure_attachments_column_added(test_engine):
    if test_engine.dialect.name != "mysql":
        pytest.skip("仅 MySQL 测 ALTER TABLE 路径")
    with test_engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE change_requests DROP COLUMN attachments"))
        except Exception:
            pass
    assert not _column_exists(test_engine, "change_requests", "attachments")

    _ensure_schema_migrations(test_engine)

    assert _column_exists(test_engine, "change_requests", "attachments")


def test_ensure_mode_column_added(test_engine):
    if test_engine.dialect.name != "mysql":
        pytest.skip("仅 MySQL")
    with test_engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE change_requests DROP COLUMN mode"))
        except Exception:
            pass
    assert not _column_exists(test_engine, "change_requests", "mode")

    _ensure_schema_migrations(test_engine)

    assert _column_exists(test_engine, "change_requests", "mode")


def test_ensure_refine_of_column_added(test_engine):
    if test_engine.dialect.name != "mysql":
        pytest.skip("仅 MySQL")
    with test_engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE change_requests DROP COLUMN refine_of"))
        except Exception:
            pass
    assert not _column_exists(test_engine, "change_requests", "refine_of")

    _ensure_schema_migrations(test_engine)

    assert _column_exists(test_engine, "change_requests", "refine_of")


def test_ensure_self_heal_attempts_column_added(test_engine):
    if test_engine.dialect.name != "mysql":
        pytest.skip("仅 MySQL")
    with test_engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE change_requests DROP COLUMN self_heal_attempts"))
        except Exception:
            pass
    assert not _column_exists(test_engine, "change_requests", "self_heal_attempts")

    _ensure_schema_migrations(test_engine)

    assert _column_exists(test_engine, "change_requests", "self_heal_attempts")


def test_migration_idempotent_second_run_noop(test_engine):
    """跑两次不出错；第二次是 noop。"""
    if test_engine.dialect.name != "mysql":
        pytest.skip("仅 MySQL")
    _ensure_schema_migrations(test_engine)
    _ensure_schema_migrations(test_engine)
    for col in ("attachments", "mode", "refine_of", "self_heal_attempts"):
        assert _column_exists(test_engine, "change_requests", col)
