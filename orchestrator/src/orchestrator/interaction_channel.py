"""SSEInteractionChannel —— 把 InteractionSkill 的「问业务员」桥接到 SSE + /answer 端点。

ask/ask_rich/present_form/present_variants 发一个 question/form/variants 事件、
生成一个 question_id、挂起等待；REST 的 /answer 端点收到回答后调
submit_answer(question_id, answer) 唤醒。

Phase C：submit_answer 在唤醒 future 后追加发一个 `question-resolved` 事件，
告诉 SSE 订阅者「这个 question_id 不要再展示了」。它同样写进 EventBus.buffer，
让 SSE 重连回放历史时也能消化掉旧问题，避免业务员重连后看到已答问题闪现。

form 答案：业务员一次提交多题，前端把答案 JSON 编码塞 `answer` 字段（API 不变），
本类用 `_parse_form_answer` 解回 dict。
"""
import asyncio
import json
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
        return await self.ask_rich(question, options, recommended=None)

    async def ask_rich(
        self, question: str, options: list[str] | None,
        *, recommended: str | None = None,
    ) -> str:
        """带 recommended 标签的单题。老调用方走 ask() 不需要改。"""
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
                    "recommended": recommended,
                },
            ),
        )
        return await fut

    async def present_form(self, questions: list[dict]) -> dict:
        """一次问 1-4 个相互独立的题，等业务员一次性提交。

        questions: [{"question": str, "options": list[str], "multi": bool,
                     "recommended": str | None}, ...]

        返回 dict {"问题文本": "答" 或 ["答1", "答2"], ...}
        业务员按 STOP 时返 {"__stop__": True}。
        """
        question_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[question_id] = fut
        await self._bus.publish(
            self._request_id,
            Event(
                type="form",
                data={
                    "question_id": question_id,
                    "questions": questions,
                },
            ),
        )
        raw = await fut
        return _parse_form_answer(raw, questions)

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


def _parse_form_answer(raw: str, questions: list[dict]) -> dict:
    """从 /answer 字符串里抠出 form 答案字典。

    前端约定：form 答案是 JSON 字符串 {"问题文本": "答" 或 ["a","b"]}。
    业务员按「✓ 够了直接干」时 raw == "__STOP_CLARIFY__"。
    解析失败时把 raw 当兜底，挂到第一题上（不丢业务员输入）。
    """
    if raw == "__STOP_CLARIFY__":
        return {"__stop__": True}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        # 老前端发字符串 / 解析失败 → 兜底塞第一题
        if questions:
            return {questions[0]["question"]: raw or ""}
        return {}
    if not isinstance(data, dict):
        return {questions[0]["question"]: raw or ""} if questions else {}
    # 把 list/value 规整成 str（multi-select 用 " / " join）
    out: dict = {}
    for q in questions:
        v = data.get(q["question"])
        if isinstance(v, list):
            out[q["question"]] = " / ".join(str(x) for x in v if str(x).strip())
        elif v is not None:
            out[q["question"]] = str(v)
    return out
