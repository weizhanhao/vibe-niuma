import asyncio

from orchestrator.quota import QuotaManager


async def test_acquire_within_capacity_does_not_block():
    q = QuotaManager(capacity=2)
    await asyncio.wait_for(q.acquire("r1"), timeout=1)
    await asyncio.wait_for(q.acquire("r2"), timeout=1)
    assert q.in_use() == 2


async def test_acquire_beyond_capacity_blocks_until_release():
    q = QuotaManager(capacity=1)
    await q.acquire("r1")
    waiting = asyncio.create_task(q.acquire("r2"))
    await asyncio.sleep(0.02)
    assert not waiting.done()  # r2 还在排队
    q.release("r1")
    await asyncio.wait_for(waiting, timeout=1)
    assert q.in_use() == 1  # r1 已放，r2 已占


async def test_release_is_idempotent():
    q = QuotaManager(capacity=1)
    await q.acquire("r1")
    q.release("r1")
    q.release("r1")  # 再次 release 不报错、不多放槽
    q.release("unknown")  # 释放未知 id 也安全
    assert q.in_use() == 0


async def test_waiting_count_reflects_queue():
    q = QuotaManager(capacity=1)
    await q.acquire("r1")
    t2 = asyncio.create_task(q.acquire("r2"))
    t3 = asyncio.create_task(q.acquire("r3"))
    await asyncio.sleep(0.02)
    assert q.waiting() == 2
    q.release("r1")
    q.release("r2")
    await asyncio.wait_for(asyncio.gather(t2, t3), timeout=1)
    assert q.waiting() == 0
