"""BrainstormingSkill —— 自适应澄清：轻路径文字问答；重路径 HTML 方案选择。

设计文档 §4.3 / §4.6：
- 轻改动：一次一问，最多 max_questions（默认 3），可跳过。
- 重改动：生成 2-3 套独立 HTML mockup（一次性意图锚点，不是真实构建产物），
  让业务员挑一套或全否。
- prompt 层硬编码「只问业务结果，不涉及任何技术」约束 —— 是契约级约定。

LLM 一次给出计划：{"weight": "light"|"heavy", "questions": [...], "variants": [...]}。
JSON 解析失败时降级走轻路径 + 跳过。
"""
from __future__ import annotations

import json
import re
import uuid

from orchestrator.adapters.impl._llm import LLMClient
from orchestrator.adapters.interfaces import InteractionChannel
from orchestrator.adapters.types import HtmlMockup, RawRequest, RequestBrief


# 公开常量：测试断言用。改这里也意味着改契约。
TECH_CONSTRAINT = (
    "你只能问业务问题，绝对禁止涉及任何技术细节"
    "（不要提组件名、文件名、CSS 属性、framework、状态管理、API 等）。"
    "你的目标是让业务员把业务效果说清楚。"
)


class BrainstormingSkill:
    """实现 InteractionSkill Protocol。"""

    def __init__(self, llm: LLMClient, *, max_questions: int = 3):
        self._llm = llm
        self._max_questions = max_questions

    async def clarify(
        self, raw: RawRequest, channel: InteractionChannel
    ) -> RequestBrief:
        plan = await self._plan(raw)
        clarifications: list[dict] = []
        selected_mockup: HtmlMockup | None = None

        if plan.get("weight") == "heavy" and plan.get("variants"):
            mockups = [
                HtmlMockup(
                    id=v.get("id") or uuid.uuid4().hex[:8],
                    title=v.get("title") or "方案",
                    html=v.get("html") or "",
                )
                for v in plan["variants"][:3]
            ]
            selection = await channel.present_variants(mockups)
            if selection.selected_id:
                selected_mockup = next(
                    (m for m in mockups if m.id == selection.selected_id), None
                )
        else:
            questions = plan.get("questions", [])[: self._max_questions]
            for q in questions:
                if not isinstance(q, str) or not q.strip():
                    continue
                answer = await channel.ask(q, None)
                if answer and answer.strip() and answer.strip() != "跳过":
                    clarifications.append({"question": q, "answer": answer})

        return RequestBrief(
            original_text=raw.request_text,
            clarifications=clarifications,
            selected_mockup=selected_mockup,
        )

    # ── plan ────────────────────────────────────────────────────────
    async def _plan(self, raw: RawRequest) -> dict:
        prompt = self._build_plan_prompt(raw)
        try:
            text = await self._llm.complete_vision(prompt, raw.screenshot_b64)
        except Exception:
            return {"weight": "light", "questions": []}
        return _safe_parse_json(text)

    def _build_plan_prompt(self, raw: RawRequest) -> str:
        return (
            f"{TECH_CONSTRAINT}\n\n"
            "下面是业务员对一个 web 页面的截图（含他框选的区域），以及他用自然语言描述的需求。\n"
            "请你判断改动是「轻」还是「重」，并按下面的 JSON 格式返回（不要任何额外文字）：\n"
            '{\n'
            '  "weight": "light" | "heavy",\n'
            '  "questions": ["业务问题 1", "业务问题 2", ...],   // 仅 light 给\n'
            '  "variants": [{"id":"v1","title":"...","html":"<style+html 单一文件>"}]  // 仅 heavy 给 2-3 个\n'
            '}\n\n'
            "判定原则：\n"
            "- 轻：颜色 / 文案 / 单字段调整。给 ≤3 个澄清问题。\n"
            "- 重：布局重排 / 增减区块 / 信息架构调整。给 2-3 套独立的 HTML mockup（每个含 inline style）作为方向锚点。\n\n"
            f"业务员的需求：{raw.request_text}\n"
            f"页面 URL：{raw.url}\n"
            f"框选坐标：{raw.box_coords}\n"
        )


_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _safe_parse_json(text: str) -> dict:
    """模型返回的 JSON 可能被包了 markdown 代码块；尽量抠出来。"""
    if not text:
        return {"weight": "light", "questions": []}
    m = _JSON_BLOCK.search(text)
    if not m:
        return {"weight": "light", "questions": []}
    try:
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return {"weight": "light", "questions": []}
        return data
    except json.JSONDecodeError:
        return {"weight": "light", "questions": []}
