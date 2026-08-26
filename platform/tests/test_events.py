import asyncio
import json

from vplatform.core.events import EventBus, reset_bus
from vplatform.core.models import Event


def test_publish_persists_then_fans_out(session, project):
    bus = EventBus(redis_url="")

    async def go():
        a = await bus.publish(project_id=project.id, stream=f"req:{project.id}",
                              kind="log", payload={"line": "hello"})
        b = await bus.publish(project_id=project.id, stream=f"req:{project.id}",
                              kind="status", payload={"stage": "build"})
        return a, b

    a, b = asyncio.run(go())
    assert b.id > a.id                      # 自增 id 是回放锚点
    assert session.query(Event).count() == 2


def test_subscribe_replays_history_then_streams_live(session, project):
    """断线重连不丢事件：带 last_event_id 回来，先补历史再跟实时。"""
    bus = EventBus(redis_url="")
    stream = f"req:{project.id}"

    async def go():
        e1 = await bus.publish(project_id=project.id, stream=stream, kind="log",
                               payload={"n": 1})
        e2 = await bus.publish(project_id=project.id, stream=stream, kind="log",
                               payload={"n": 2})

        got = []
        # 客户端说「我收到 e1 了」→ 应只补 e2，然后跟实时
        agen = bus.subscribe(project_id=project.id, stream=stream, last_event_id=e1.id)

        async def consume():
            async for ev in agen:
                got.append(ev)
                if len(got) == 2:
                    break

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        e3 = await bus.publish(project_id=project.id, stream=stream, kind="log",
                               payload={"n": 3})
        await asyncio.wait_for(task, timeout=2)
        return got, e1, e2, e3

    got, e1, e2, e3 = asyncio.run(go())
    assert [g.id for g in got] == [e2.id, e3.id]   # 不含已收到的 e1
    assert [g.payload["n"] for g in got] == [2, 3]


def test_no_duplicate_across_replay_boundary(session, project):
    """回放期间并发进来的事件不能重复推。"""
    bus = EventBus(redis_url="")
    stream = f"req:{project.id}"

    async def go():
        for i in range(3):
            await bus.publish(project_id=project.id, stream=stream, kind="log",
                              payload={"n": i})
        got = []
        agen = bus.subscribe(project_id=project.id, stream=stream, last_event_id=0)

        async def consume():
            async for ev in agen:
                got.append(ev.id)
                if len(got) == 4:
                    break

        t = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await bus.publish(project_id=project.id, stream=stream, kind="log", payload={"n": 3})
        await asyncio.wait_for(t, timeout=2)
        return got

    got = asyncio.run(go())
    assert len(got) == len(set(got)) == 4


def test_sse_frame_shape(session, project):
    bus = EventBus(redis_url="")
    ev = asyncio.run(bus.publish(project_id=project.id, stream="s", kind="log",
                                 payload={"line": "中文也要对"}))
    frame = ev.sse()
    assert frame.startswith(f"id: {ev.id}\nevent: log\ndata: ")
    assert "中文也要对" in frame            # 不能被 ascii 转义成 \uXXXX
    assert frame.endswith("\n\n")


def test_falls_back_to_local_when_redis_missing(session, project, monkeypatch):
    """配了 redis_url 但没装包 —— 退化为单进程，不能崩。"""
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("redis"):
            raise ImportError("no redis")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    bus = EventBus(redis_url="redis://localhost:6379/0")
    ev = asyncio.run(bus.publish(project_id=project.id, stream="s", kind="log"))
    assert ev.id > 0
    reset_bus()


def test_late_committing_event_is_not_lost_at_replay_boundary(session, project):
    """**H9 回归 —— 事件永久丢失。**

    自增 id 的分配顺序 ≠ 提交顺序。长事务 A 先拿到小 id 但后提交，
    短事务 B 拿大 id 先提交。订阅者回放只看到 B，若用阈值游标，
    A 到达时被判为"旧的"丢弃；客户端带 lastEventId=B.id 重连也补不回来。
    """
    from vplatform.core.db import session_scope
    from vplatform.core.models import Event as EventRow

    bus = EventBus(redis_url="")
    stream = f"req:{project.id}"

    async def go():
        # 模拟：A 先拿 id（未提交），B 后拿 id 但先提交
        with session_scope() as s:
            a = bus.record(s, project_id=project.id, stream=stream,
                           kind="log", payload={"who": "A-长事务"})
        with session_scope() as s:
            b = bus.record(s, project_id=project.id, stream=stream,
                           kind="log", payload={"who": "B-短事务"})
        assert a.id < b.id

        got = []
        # 订阅者此刻连上：回放会看到 A 和 B（都已落库）
        agen = bus.subscribe(project_id=project.id, stream=stream, last_event_id=a.id)

        async def consume():
            async for ev in agen:
                got.append(ev)
                if len(got) == 2:
                    break

        t = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        # A 的 dispatch 迟到 —— 它的 id 小于已回放的 B
        await bus.dispatch(a)
        # 再来一条新的
        c = await bus.publish(project_id=project.id, stream=stream, kind="log",
                              payload={"who": "C"})
        await asyncio.wait_for(t, timeout=2)
        return got, a, b, c

    got, a, b, c = asyncio.run(go())
    ids = [g.id for g in got]
    assert b.id in ids, "回放里应有 B"
    assert c.id in ids, "迟到的 A 不能挤掉后来的 C"
    assert len(ids) == len(set(ids)), "不能重复推送"


def test_client_supplied_cursor_is_still_respected(session, project):
    """客户端说「我收到 N 了」，就不该再收到 <= N 的。"""
    bus = EventBus(redis_url="")
    stream = f"req:{project.id}"

    async def go():
        e1 = await bus.publish(project_id=project.id, stream=stream, kind="log",
                               payload={"n": 1})
        e2 = await bus.publish(project_id=project.id, stream=stream, kind="log",
                               payload={"n": 2})
        got = []
        agen = bus.subscribe(project_id=project.id, stream=stream, last_event_id=e1.id)

        async def consume():
            async for ev in agen:
                got.append(ev.id)
                if got:
                    break

        t = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await asyncio.wait_for(t, timeout=2)
        return got, e1, e2

    got, e1, e2 = asyncio.run(go())
    assert e1.id not in got
    assert got == [e2.id]


# ── 思考流（只走实时通道，不落库）────────────────────────────────
def test_live_events_do_not_touch_the_database(session, project):
    """agent 的思考一次运行几十上百条，而且它跑的时候 worker 的事务正开着。
    每条都落库 = dual-write：中途崩溃留不一致，sqlite 上直接死锁。"""
    from vplatform.core.models import Event as EventRow
    bus = EventBus()
    before = session.query(EventRow).count()
    for i in range(50):
        bus.publish_live(stream="req:r1", kind="agent_step", payload={"text": f"第{i}步"})
    assert session.query(EventRow).count() == before


def test_live_events_have_no_sse_id(session, project):
    """**临时事件不能带 id。**

    浏览器 EventSource 会把收到的最后一个 id 记成 lastEventId，断线重连时
    带回来。临时事件 id 是 0，带上就把游标重置成 0 —— 重连后整条历史重放一遍。
    """
    from vplatform.core.events import Emitted
    live = Emitted(id=0, stream="s", kind="agent_step", payload={"text": "x"},
                   ephemeral=True)
    real = Emitted(id=7, stream="s", kind="status", payload={"stage": "verify"})
    assert not live.sse().startswith("id:")
    assert real.sse().startswith("id: 7")


def test_a_late_subscriber_sees_what_already_happened(session, project):
    """中途打开页面的人要看得到这次运行已经吐出来的部分，
    不然他只看到一个空面板，以为什么都没发生。"""
    bus = EventBus()
    for i in range(3):
        bus.publish_live(stream=f"req:{project.id}", kind="agent_step",
                         payload={"text": f"第{i}步"})

    async def go():
        agen = bus.subscribe(project_id=project.id, stream=f"req:{project.id}")
        got = []
        try:
            for _ in range(3):
                got.append(await asyncio.wait_for(agen.__anext__(), timeout=2))
        finally:
            await agen.aclose()
        return got

    got = asyncio.run(go())
    assert [g.payload["text"] for g in got] == ["第0步", "第1步", "第2步"]


def test_the_live_buffer_is_bounded(session, project):
    """不设上限的话一次长跑就把内存吃了。"""
    from vplatform.core.events import _LIVE_BUFFER
    bus = EventBus()
    for i in range(_LIVE_BUFFER + 120):
        bus.publish_live(stream="req:r1", kind="agent_step", payload={"i": i})
    buf = bus.live_backlog("req:r1")
    assert len(buf) == _LIVE_BUFFER
    assert buf[-1].payload["i"] == _LIVE_BUFFER + 119    # 留的是最新的


def test_a_new_run_does_not_show_the_previous_ones_thinking(session, project):
    bus = EventBus()
    bus.publish_live(stream="req:r1", kind="agent_step", payload={"text": "上一次"})
    bus.clear_live("req:r1")
    assert bus.live_backlog("req:r1") == []


# ── 跨进程实时（worker 和 API 不在一个进程里）──────────────────
def test_redis_events_from_another_process_reach_subscribers(session, project,
                                                             monkeypatch):
    """**worker 和 API 是两个进程。**

    之前 dispatch() 往 Redis xadd，但没有任何地方 XREAD —— 写进去没人读，
    跨进程实时等于不存在：订阅者只看得到连接那一刻从 MySQL 补的历史，
    worker 之后发的一条都收不到。
    """
    sent: list[dict] = []

    class FakeRedis:
        async def xadd(self, key, fields, **kw):
            sent.append({"key": key, **fields})

        async def xread(self, streams, count=64, block=0):
            if not sent:
                await asyncio.sleep(0.01)
                return []
            key = next(iter(streams))
            out = [(key, [(f"1-{i}", {"d": d["d"]}) for i, d in enumerate(sent)])]
            sent.clear()
            return out

        async def aclose(self):
            pass

    bus = EventBus(redis_url="redis://fake")
    monkeypatch.setattr(bus, "_get_redis", lambda: _done(FakeRedis()))
    # 别的进程发的：src 不是本实例
    sent.append({"d": json.dumps({"id": 0, "kind": "agent_step", "ephemeral": True,
                                  "src": "另一个进程",
                                  "payload": {"text": "读文件：exporter.py"}})})

    async def go():
        agen = bus.subscribe(project_id=project.id, stream=f"req:{project.id}")
        try:
            return await asyncio.wait_for(agen.__anext__(), timeout=3)
        finally:
            await agen.aclose()

    ev = asyncio.run(go())
    assert ev.payload["text"] == "读文件：exporter.py" and ev.ephemeral


def test_our_own_events_do_not_come_back_twice(session, project, monkeypatch):
    """Redis 会把自己发的事件再送回来。持久事件靠 id 去重，
    临时事件 id 都是 0 去不了重 —— 不挡的话页面上每条思考出现两遍。"""
    bus = EventBus(redis_url="redis://fake")
    frames: list[str] = []

    class FakeRedis:
        async def xadd(self, key, fields, **kw):
            frames.append(fields["d"])

        async def xread(self, streams, count=64, block=0):
            # 空的时候要「阻塞」一下。真 Redis 的 block 就是这个语义；
            # 立刻返回空会把事件循环转成忙等
            if not frames:
                await asyncio.sleep(0.02)
                return []
            key = next(iter(streams))
            out = [(key, [(f"1-{i}", {"d": f}) for i, f in enumerate(frames)])]
            frames.clear()
            return out

        async def aclose(self):
            pass

    monkeypatch.setattr(bus, "_get_redis", lambda: _done(FakeRedis()))

    async def go():
        agen = bus.subscribe(project_id=project.id, stream="req:x")
        got = []
        try:
            await asyncio.sleep(0.05)          # 让订阅挂上
            bus.publish_live(stream="req:x", kind="agent_step",
                             payload={"text": "只该出现一次"})
            got.append(await asyncio.wait_for(agen.__anext__(), timeout=2))
            try:
                got.append(await asyncio.wait_for(agen.__anext__(), timeout=0.6))
            except asyncio.TimeoutError:
                pass
        finally:
            await agen.aclose()
        return got

    got = asyncio.run(go())
    assert len(got) == 1, f"同一条思考收到 {len(got)} 次"


def _done(value):
    """把普通值包成已完成的 awaitable，替 _get_redis 用。"""
    async def _c():
        return value
    return _c()


def test_a_quiet_window_does_not_kill_the_stream(session, project, monkeypatch):
    """**XREAD 超时不是错误，是「这段时间没有新事件」。**

    当成错误退出的话，第一个安静的窗口就把推流永久掐断 —— agent 后面吐的
    思考一条都到不了浏览器。实测就是这样：worker 那边 Redis 里躺着 16 条，
    页面上一条没有。
    """
    import redis.exceptions as rexc

    calls = {"n": 0}

    class FlakyRedis:
        async def xadd(self, *a, **kw):
            pass

        async def xread(self, streams, count=64, block=0):
            calls["n"] += 1
            if calls["n"] == 1:
                raise rexc.TimeoutError("Timeout reading from 127.0.0.1:6379")
            if calls["n"] == 2:
                key = next(iter(streams))
                return [(key, [("1-1", {"d": json.dumps(
                    {"id": 0, "kind": "agent_step", "ephemeral": True,
                     "src": "别的进程", "payload": {"text": "安静之后还能收到"}})})])]
            await asyncio.sleep(0.05)
            return []

        async def aclose(self):
            pass

    bus = EventBus(redis_url="redis://fake")
    monkeypatch.setattr(bus, "_get_redis", lambda: _done(FlakyRedis()))

    async def go():
        agen = bus.subscribe(project_id=project.id, stream="req:quiet")
        try:
            return await asyncio.wait_for(agen.__anext__(), timeout=3)
        finally:
            await agen.aclose()

    ev = asyncio.run(go())
    assert ev.payload["text"] == "安静之后还能收到"
    assert calls["n"] >= 2, "超时之后没有继续读"


def test_a_late_subscriber_in_another_process_still_sees_the_thinking(
        session, project, monkeypatch):
    """**跨进程时本地缓冲是空的。**

    思考缓冲在 worker 进程的内存里，SSE 挂在 API 进程 —— 晚连上来的客户端
    从 `$` 开始读 Redis，前面已经吐出来的思考全丢，页面上就是
    「正在想… 0 步」。用户截图里就是这个。
    """
    entries: list[tuple[str, dict]] = []

    def _frame(kind, text, src="worker进程"):
        return {"d": json.dumps({"id": 0, "kind": kind, "ephemeral": True,
                                 "src": src, "payload": {"text": text}})}

    entries.append(("1-0", _frame("run_start", "")))
    entries.append(("1-1", _frame("tool", "读文件：exporter.py")))
    entries.append(("1-2", _frame("reasoning", "先看看导出是怎么实现的")))

    class FakeRedis:
        async def xrevrange(self, key, a, b, count=None):
            return list(reversed(entries))

        async def xread(self, streams, count=64, block=0):
            await asyncio.sleep(0.02)
            return []

        async def xadd(self, *a, **kw):
            pass

        async def aclose(self):
            pass

    bus = EventBus(redis_url="redis://fake")
    monkeypatch.setattr(bus, "_get_redis", lambda: _done(FakeRedis()))

    async def go():
        agen = bus.subscribe(project_id=project.id, stream="req:late")
        got = []
        try:
            for _ in range(2):
                got.append(await asyncio.wait_for(agen.__anext__(), timeout=3))
        finally:
            await agen.aclose()
        return got

    got = asyncio.run(go())
    assert [g.payload["text"] for g in got] == ["读文件：exporter.py", "先看看导出是怎么实现的"]


def test_replay_stops_at_the_current_run(session, project, monkeypatch):
    """只补本次运行的 —— 上一次运行的残留不该混进来。"""
    def _frame(kind, text):
        return {"d": json.dumps({"id": 0, "kind": kind, "ephemeral": True,
                                 "src": "别的进程", "payload": {"text": text}})}

    entries = [("1-0", _frame("tool", "上一次的")),
               ("1-1", _frame("run_start", "")),
               ("1-2", _frame("tool", "这一次的"))]

    class FakeRedis:
        async def xrevrange(self, key, a, b, count=None):
            return list(reversed(entries))
        async def xread(self, streams, count=64, block=0):
            await asyncio.sleep(0.02); return []
        async def xadd(self, *a, **kw): pass
        async def aclose(self): pass

    bus = EventBus(redis_url="redis://fake")
    monkeypatch.setattr(bus, "_get_redis", lambda: _done(FakeRedis()))

    async def go():
        agen = bus.subscribe(project_id=project.id, stream="req:x")
        try:
            first = await asyncio.wait_for(agen.__anext__(), timeout=3)
            try:
                second = await asyncio.wait_for(agen.__anext__(), timeout=0.6)
            except asyncio.TimeoutError:
                second = None
            return first, second
        finally:
            await agen.aclose()

    first, second = asyncio.run(go())
    assert first.payload["text"] == "这一次的"
    assert second is None, "把上一次运行的思考也回放了"


def test_run_start_is_never_shown_to_the_client(session, project):
    """内部标记漏出去的话，页面上会多一条空白「步骤」。"""
    bus = EventBus()
    bus.clear_live("req:x")                     # 会打一个 run_start
    bus.publish_live(stream="req:x", kind="tool", payload={"text": "读文件"})
    kinds = [e.kind for e in bus.live_backlog("req:x")]
    assert kinds == ["tool"]
