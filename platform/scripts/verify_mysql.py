#!/usr/bin/env python3
"""MySQL 专属路径验证。

这些实现只在 MySQL 上执行，sqlite 测试碰不到它们（`jobs.py:97` 显式
对 sqlite 关掉 SKIP LOCKED）。第一次 `docker compose up` 不该是第一次验证。

    VP_DATABASE_URL=mysql+pymysql://... python scripts/verify_mysql.py
"""
from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import inspect, text                       # noqa: E402

from vplatform.core.db import get_engine, init_engine, session_scope  # noqa: E402
from vplatform.core.models import (                        # noqa: E402
    Job, Member, Org, Project, next_requirement_seq,
)
from vplatform.orchestration.jobs import JobStore          # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f" —— {detail}" if detail else ""))
    return ok


def main() -> int:
    eng = init_engine()
    if eng.dialect.name != "mysql":
        print(f"跳过：当前不是 MySQL（{eng.dialect.name}）")
        return 0

    results: list[bool] = []
    print("════ MySQL 专属路径验证 ════")

    # 1. 表全建出来了（迁移已跑）
    tables = set(inspect(eng).get_table_names())
    from vplatform.core.models import Base
    expected = set(Base.metadata.tables)
    results.append(check("21 张表齐全", expected <= tables,
                         f"缺：{sorted(expected - tables)}" if expected - tables else ""))

    # 2. utf8mb4 下的索引长度真的建得出来（静态算过 2208 字节，这里是实证）
    with eng.connect() as c:
        rf = c.execute(text(
            "SELECT row_format FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='task_touches'")).scalar()
    results.append(check("task_touches 是 DYNAMIC row_format", rf == "Dynamic", str(rf)))

    # 3. Event.id 是 BIGINT（INT 的 21 亿上限约两年触顶）
    with eng.connect() as c:
        col = c.execute(text(
            "SELECT column_type FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='events' AND column_name='id'"
        )).scalar()
    results.append(check("events.id 是 bigint", "bigint" in str(col).lower(), str(col)))

    # 4. LONGTEXT variant 生效
    with eng.connect() as c:
        col = c.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='requirements' "
            "AND column_name='body'")).scalar()
    results.append(check("大文本用 longtext", str(col).lower() == "longtext", str(col)))

    # 5. 造一个空间 + 一批 job
    with session_scope() as s:
        s.execute(text("DELETE FROM jobs"))
        org = s.execute(text("SELECT id FROM orgs LIMIT 1")).scalar()
        if not org:
            o = Org(name="ci"); s.add(o); s.flush(); org = o.id
        p = s.execute(text("SELECT id FROM projects WHERE slug='ci'")).scalar()
        if not p:
            pr = Project(org_id=org, name="ci", slug="ci"); s.add(pr); s.flush(); p = pr.id
        st = JobStore(s)
        for i in range(20):
            st.enqueue(project_id=p, kind="noop", idempotency_key=f"v{i}")
        pid = p

    # 6. **SKIP LOCKED 并发抢占** —— sqlite 上这条路径从没执行过
    def grab(worker: str) -> list[str]:
        got = []
        for _ in range(10):
            with session_scope() as s:
                job = JobStore(s).claim(worker_id=worker)
                if job is None:
                    break
                got.append(job.id)
        return got

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        batches = list(ex.map(grab, [f"w{i}" for i in range(4)]))
    allocated = [j for b in batches for j in b]
    results.append(check("SKIP LOCKED 并发抢占无重复",
                         len(allocated) == len(set(allocated)),
                         f"{len(allocated)} 个，去重后 {len(set(allocated))} 个"))
    results.append(check("抢占覆盖了全部 job", len(set(allocated)) == 20,
                         f"抢到 {len(set(allocated))}/20"))

    # 7. 每空间编号的原子自增（InnoDB 行锁）
    def seq() -> int:
        with session_scope() as s:
            return next_requirement_seq(s, pid)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        nums = list(ex.map(lambda _: seq(), range(24)))
    results.append(check("需求编号并发不重号",
                         len(nums) == len(set(nums)), f"{sorted(nums)[:6]}…"))

    print()
    ok = all(results)
    print(f"{'✓ 全部通过' if ok else '✗ 有失败项'}（{sum(results)}/{len(results)}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
