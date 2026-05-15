"""SSEInteractionChannel —— 把 InteractionSkill 的「问业务员」桥接到 SSE + /answer 端点。

ask/present_variants 发一个 question/variants 事件、生成一个 question_id、挂起等待；
REST 的 /answer 端点收到回答后调 submit_answer(question_id, answer) 唤醒。
"""
import asyncio
import uuid

from orchestrator.adapters.types import HtmlMockup, VariantSelection
from orchestrator.events import Event, EventBus


class SSEInteractionChannel:
    def __init__(self, request_id: str, event_bus: EventBus):
        self._request_id = request_id
        self._bus = event_bus
        self._pending: dict[str, asyncio.Future] = {}

    async def ask(self, question: str, options: list[str] | None) -> str:
        question_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[question_id] = fut
        await self._bus.publish(
            self._request_id,
            Event(
                type="question",
                data={
                    "question_id": question_id,
                    "question": question,
                    "options": options,
                },
            ),
        )
        return await fut

    async def present_variants(
        self, variants: list[HtmlMockup]
    ) -> VariantSelection:
        question_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[question_id] = fut
        await self._bus.publish(
            self._request_id,
            Event(
                type="variants",
                data={
                    "question_id": question_id,
                    "variants": [
                        {"id": v.id, "title": v.title, "html": v.html}
                        for v in variants
                    ],
                },
            ),
        )
        selected_id = await fut
        return VariantSelection(selected_id=selected_id or None)

    def submit_answer(self, question_id: str, answer: str) -> None:
        fut = self._pending.pop(question_id, None)
        if fut is not None and not fut.done():
            fut.set_result(answer)
