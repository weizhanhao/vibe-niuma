import asyncio

from orchestrator.adapters.types import HtmlMockup
from orchestrator.events import EventBus
from orchestrator.interaction_channel import SSEInteractionChannel


async def test_ask_publishes_question_event_and_waits_for_answer():
    bus = EventBus()
    channel = SSEInteractionChannel(request_id="r1", event_bus=bus)
    sub = bus.subscribe("r1")

    ask_task = asyncio.create_task(channel.ask("你想要什么效果？", None))
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.type == "question"
    assert evt.data["question"] == "你想要什么效果？"
    qid = evt.data["question_id"]

    assert not ask_task.done()  # 还在等回答
    channel.submit_answer(qid, "更显眼")
    answer = await asyncio.wait_for(ask_task, timeout=1)
    assert answer == "更显眼"


async def test_submit_answer_publishes_question_resolved_event():
    """Phase C：回答后应当广播 question-resolved，让 SSE 重连不再回放旧问题。"""
    bus = EventBus()
    channel = SSEInteractionChannel(request_id="r-c", event_bus=bus)
    sub = bus.subscribe("r-c")

    ask_task = asyncio.create_task(channel.ask("要红色还是蓝色？", ["红色", "蓝色"]))
    q_evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    qid = q_evt.data["question_id"]

    channel.submit_answer(qid, "红色")
    await asyncio.wait_for(ask_task, timeout=1)

    resolved_evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert resolved_evt.type == "question-resolved"
    assert resolved_evt.data["question_id"] == qid

    # 也要进 buffer：晚到/重连订阅者通过回放消化掉它
    history = bus.history("r-c")
    assert any(
        e.type == "question-resolved" and e.data["question_id"] == qid for e in history
    )


async def test_submit_answer_for_unknown_question_does_not_publish_resolved():
    """未知 question_id（过期/重复回答）不应污染事件流。"""
    bus = EventBus()
    channel = SSEInteractionChannel(request_id="r-c2", event_bus=bus)

    channel.submit_answer("no-such-qid", "whatever")
    # 给 asyncio.create_task 一个调度窗口（即使它本应没创建任务）
    await asyncio.sleep(0)

    assert bus.history("r-c2") == []


async def test_present_variants_publishes_event_and_waits_for_selection():
    bus = EventBus()
    channel = SSEInteractionChannel(request_id="r2", event_bus=bus)
    sub = bus.subscribe("r2")

    variants = [HtmlMockup(id="v1", title="方案一", html="<a/>")]
    task = asyncio.create_task(channel.present_variants(variants))
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.type == "variants"
    assert evt.data["variants"][0]["id"] == "v1"
    qid = evt.data["question_id"]

    channel.submit_answer(qid, "v1")
    selection = await asyncio.wait_for(task, timeout=1)
    assert selection.selected_id == "v1"


async def test_submit_answer_for_unknown_question_is_ignored():
    bus = EventBus()
    channel = SSEInteractionChannel(request_id="r3", event_bus=bus)
    # 不抛异常即可
    channel.submit_answer("no-such-question", "whatever")
