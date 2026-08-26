#!/usr/bin/env python3
"""迁移 CLI。

    python scripts/migrate.py status     看当前版本与待应用
    python scripts/migrate.py upgrade    应用全部待应用
    python scripts/migrate.py plan       只打印要跑什么，不执行
    python scripts/migrate.py baseline   把现有 schema 标为已应用（老库接入用）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vplatform.core import migrate                       # noqa: E402
from vplatform.core.db import init_engine                # noqa: E402


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    engine = init_engine()

    if cmd == "status":
        st = migrate.status(engine)
        print(f"当前版本：{st['current'] or '（空库）'}")
        print(f"已应用 {len(st['applied'])} 条")
        if st["pending"]:
            print("待应用：")
            for p in st["pending"]:
                print("  ", p)
        else:
            print("没有待应用的迁移")
        return 0

    if cmd == "plan":
        for name in migrate.upgrade(engine, dry_run=True):
            print("将应用：", name)
        return 0

    if cmd == "upgrade":
        done = migrate.upgrade(engine)
        print(f"应用了 {len(done)} 条迁移" if done else "没有待应用的迁移")
        for d in done:
            print("  ✓", d)
        return 0

    if cmd == "baseline":
        from datetime import datetime
        from sqlalchemy import text
        migrate.applied(engine)
        with engine.begin() as c:
            for v, slug, path in migrate.discover():
                c.execute(text(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum)"
                    " VALUES (:v,:n,:t,:c)"),
                    {"v": v, "n": slug, "t": datetime.utcnow(),
                     "c": migrate._checksum(path.read_text(encoding='utf-8'))})
        print("已把全部迁移标为已应用（未执行 DDL）")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
