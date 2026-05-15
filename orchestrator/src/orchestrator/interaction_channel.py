"""SSEInteractionChannel —— 把 InteractionSkill 的「问业务员」桥接到 SSE + /answer 端点。

ask/present_variants 发一个 question/variants 事件、生成一个 question_id、挂起等待；
REST 的 /answer 端点收到回答后调 submit_answer(question_id, answer) 唤醒。

Phase C：submit_answer 在唤醒 future 后追加发一个 `question-resolved` 事件，
告诉 SSE 订阅者「这个 question_id 不要再展示了」。它同样写进 EventBus.buffer，
让 SSE 重连回放历史时也能消化掉旧问题，避免业务员重连后看到已答问题闪现。
"""
import asyncio
import uuid

from orchestrator.adapters.types import HtmlMockup, VariantSelection
from orchestrator.events import Event, EventBus


class SSEInteractionChannel:
    def __init__(self, request_id: str, event_bus: EventBus, phase: str = "clarifying"):
        self._request_id = request_id
        self._bus = event_bus
        self._phase = phase
        self._pending: dict[str, asyncio.Future] = {}

    async def log(self, line: str) -> None:
        """让 InteractionSkill 把粒度更细的进度信息（如 LLM token 流）回灌到 SSE。"""
        await self._bus.publish_log(self._request_id, self._phase, line)

    async def ask(self, question: str, options: list[str] | None) -> str:
        question_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
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
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
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
        if fut is None:
            # 未知 question_id（重复回答、过期问题等）不污染事件流
            return
        # Future 绑定的是 pipeline 所在的 event loop（创建 fut 时的 loop）。
        # 本方法可能从 FastAPI 同步路由的线程池里被调用 —— 此线程没有 running loop，
        # 因此用 loop.call_soon_threadsafe 设置结果 + run_coroutine_threadsafe 发广播。
        loop = fut.get_loop()
        if not fut.done():
            loop.call_soon_threadsafe(fut.set_result, answer)
        # Phase C：广播 question-resolved，让 SSE 实时订阅者与重连历史回放都消化掉这个旧问题。
        asyncio.run_coroutine_threadsafe(
            self._bus.publish(
                self._request_id,
                Event(
                    type="question-resolved",
                    data={"question_id": question_id},
                ),
            ),
            loop,
        )
