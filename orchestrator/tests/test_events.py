import asyncio

import pytest

from orchestrator.events import Event, EventBus


async def test_publish_then_subscribe_receives_event():
    bus = EventBus()
    await bus.publish("req-1", Event(type="status", data={"state": "clarifying"}))
    sub = bus.subscribe("req-1")
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.type == "status"
    assert evt.data["state"] == "clarifying"


async def test_subscribe_receives_live_events():
    bus = EventBus()
    sub = bus.subscribe("req-2")

    async def producer():
        await asyncio.sleep(0.01)
        await bus.publish("req-2", Event(type="status", data={"state": "coding"}))

    asyncio.create_task(producer())
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.data["state"] == "coding"


async def test_events_are_isolated_per_request():
    bus = EventBus()
    await bus.publish("req-a", Event(type="status", data={"x": 1}))
    await bus.publish("req-b", Event(type="status", data={"x": 2}))
    sub_b = bus.subscribe("req-b")
    evt = await asyncio.wait_for(sub_b.__anext__(), timeout=1)
    assert evt.data["x"] == 2


async def test_replayed_history_not_duplicated_then_live_event_arrives():
    bus = EventBus()
    await bus.publish("req-3", Event(type="status", data={"n": 1}))
    await bus.publish("req-3", Event(type="status", data={"n": 2}))
    sub = bus.subscribe("req-3")
    # 回放两个历史事件
    e1 = await asyncio.wait_for(sub.__anext__(), timeout=1)
    e2 = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert [e1.data["n"], e2.data["n"]] == [1, 2]
    # 再来一个新事件，应只收到这一个（历史不重复）
    await bus.publish("req-3", Event(type="status", data={"n": 3}))
    e3 = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert e3.data["n"] == 3


async def test_close_ends_subscription():
    bus = EventBus()
    sub = bus.subscribe("req-4")
    await bus.publish("req-4", Event(type="status", data={}))
    await sub.__anext__()  # consume the one event
    bus.close("req-4")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(sub.__anext__(), timeout=1)
