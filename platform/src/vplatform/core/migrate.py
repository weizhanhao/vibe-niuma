"""版本化 schema 迁移。

之前**完全没有迁移方案** —— 唯一建表途径是 `Base.metadata.create_all()`，
它只建不改。企业级平台上线后加一列就得手工 DDL，还没有任何记录说明
哪个环境跑到了哪一版。

设计取舍：不用 alembic。原因是 MySQL 没有事务性 DDL（D4 的已知缺口），
alembic 的自动生成在这种环境下反而危险 —— 它会把多条 DDL 塞进一个
"事务"里，中途失败就留下半迁移状态且无法回滚。这里强制一条 DDL 一个文件、
逐条记录，失败可以从断点手工续。

用法：
    migrations/0001_init.sql
    migrations/0002_add_jobs_archive.sql
每个文件里**只放一条 DDL**。文件名前缀决定顺序，不可改名不可删。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"
_NAME = re.compile(r"^(?P<v>\d{4})_(?P<slug>[a-z0-9_]+)\.sql$")

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     VARCHAR(8)   NOT NULL PRIMARY KEY,
  name        VARCHAR(120) NOT NULL,
  applied_at  TIMESTAMP    NOT NULL,
  checksum    VARCHAR(64)  NOT NULL,
  stmt_done   INT          NOT NULL DEFAULT 0,
  complete    TINYINT      NOT NULL DEFAULT 0
)
"""


def _progress(engine: Engine, version: str) -> int:
    """这一版已经成功执行到第几条语句（非事务性 DDL 的断点）。"""
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT stmt_done FROM schema_migrations WHERE version = :v"),
            {"v": version}).first()
    return int(row[0]) if row else 0


def split_statements(body: str) -> list[str]:
    """把迁移文件切成一条条语句。

    **不能简单 `body.split(";")` 再丢掉 `startswith("--")` 的块** ——
    文件开头的注释会和第一条语句粘在同一块里，整块被当注释丢掉，
    于是第一张表根本没建，而运行器毫无察觉。
    实测踩过：0001_init.sql 的 69 条 DDL 被当成 68 条，orgs 表消失，
    后面所有引用它的外键全部失败。**迁移器静默丢语句是最危险的一类 bug。**

    做法：先逐行剥掉整行注释，再按 `;` 切。
    """
    lines = [ln for ln in body.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _checksum(text_: str) -> str:
    import hashlib
    return hashlib.sha256(text_.encode()).hexdigest()


def discover(directory: Path | None = None) -> list[tuple[str, str, Path]]:
    d = directory or MIGRATIONS_DIR
    out: list[tuple[str, str, Path]] = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.sql")):
        m = _NAME.match(f.name)
        if not m:
            raise ValueError(f"迁移文件名不合规：{f.name}（应形如 0001_add_foo.sql）")
        out.append((m.group("v"), m.group("slug"), f))
    versions = [v for v, _, _ in out]
    if len(versions) != len(set(versions)):
        raise ValueError(f"迁移版本号重复：{versions}")
    return out


def applied(engine: Engine, *, include_partial: bool = False) -> dict[str, str]:
    """已应用的版本 → 校验和。

    默认**只算跑完的** —— 跑到一半的版本必须留在 pending 里，
    否则下次 upgrade 会跳过它，schema 永远缺一块。
    """
    with engine.begin() as c:
        c.execute(text(_LEDGER_DDL))
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT version, checksum, stmt_done, complete FROM schema_migrations")).all()
    return {r[0]: r[1] for r in rows if include_partial or r[3]}


def pending(engine: Engine, directory: Path | None = None) -> list[tuple[str, str, Path]]:
    done = applied(engine)
    partial = applied(engine, include_partial=True)
    out = []
    for version, slug, path in discover(directory):
        body = path.read_text(encoding="utf-8")
        if version in partial and partial[version] != _checksum(body):
            # **已应用（哪怕只跑了一半）的迁移内容被改过要立刻报错。**
            # 悄悄改过的迁移在新环境跑出的 schema 与老环境不同，
            # 这类漂移查起来极其痛苦。
            raise RuntimeError(
                f"迁移 {path.name} 已应用但内容被修改过。"
                f"迁移是不可变的 —— 要改就新加一个版本。")
        if version in done:
            if done[version] != _checksum(body):
                raise RuntimeError(
                    f"迁移 {path.name} 已应用但内容被修改过。"
                    f"迁移是不可变的 —— 要改就新加一个版本。")
            continue
        out.append((version, slug, path))
    return out


def upgrade(engine: Engine, directory: Path | None = None, *, dry_run: bool = False) -> list[str]:
    """逐条应用。**一条失败就停**，不继续跑后面的。"""
    todo = pending(engine, directory)
    done: list[str] = []
    from datetime import datetime

    for version, slug, path in todo:
        body = path.read_text(encoding="utf-8")
        stmts = split_statements(body)
        if dry_run:
            done.append(f"{version}_{slug}")
            continue
        logger.info("应用迁移 %s_%s（%d 条语句）", version, slug, len(stmts))
        # **逐条提交并记录进度。**
        # MySQL 的 DDL 会隐式提交，把 N 条塞进一个 `engine.begin()` 只是
        # 给人一种原子的错觉：中途失败时前面的表已经建好了，而账本的
        # INSERT 被回滚 —— 重跑就撞「表已存在」，人工收拾极其痛苦。
        # 这里记录已完成的语句序号，失败后重跑从断点续。
        start = _progress(engine, version)
        try:
            for idx, st in enumerate(stmts):
                if idx < start:
                    continue
                with engine.begin() as c:
                    c.execute(text(st))
                    c.execute(text(
                        "REPLACE INTO schema_migrations "
                        "(version, name, applied_at, checksum, stmt_done) "
                        "VALUES (:v,:n,:t,:c,:i)"),
                        {"v": version, "n": slug, "t": datetime.utcnow(),
                         "c": _checksum(body), "i": idx + 1})
        except Exception:
            logger.exception(
                "迁移 %s_%s 第 %d 条失败 —— 后续迁移不再执行。"
                "修好后重跑会从这一条续，不会重做前面的。",
                version, slug, start + 1)
            raise
        with engine.begin() as c:
            c.execute(text(
                "UPDATE schema_migrations SET complete = 1 WHERE version = :v"),
                {"v": version})
        done.append(f"{version}_{slug}")
    return done


def status(engine: Engine, directory: Path | None = None) -> dict:
    done = applied(engine)
    todo = [f"{v}_{s}" for v, s, _ in pending(engine, directory)]
    return {"applied": sorted(done), "pending": todo,
            "current": max(done) if done else None}
