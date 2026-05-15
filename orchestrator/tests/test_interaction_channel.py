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
