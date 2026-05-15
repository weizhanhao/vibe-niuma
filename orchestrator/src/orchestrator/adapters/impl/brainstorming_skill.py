"""BrainstormingSkill —— 自适应澄清：轻路径文字问答；重路径 HTML 方案选择。

设计文档 §4.3 / §4.6：
- 轻改动：一次一问，最多 max_questions（默认 3），可跳过。
- 重改动：生成 2-3 套独立 HTML mockup（一次性意图锚点，不是真实构建产物），
  让业务员挑一套或全否。
- prompt 层硬编码「只问业务结果，不涉及任何技术」约束 —— 是契约级约定。

Phase A 升级：
- 接 RepoInitializer，clarify 开始前 await /init 完成；把 AGENTS.md 内容塞 prompt。
- prompt 收紧：基于项目知识判断，只在真有歧义时问；简单改动 0 问题。

LLM 一次给出计划：{"weight": "light"|"heavy", "questions": [...], "variants": [...]}。
JSON 解析失败时降级走轻路径 + 跳过。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import TYPE_CHECKING

from orchestrator.adapters.impl._llm import LLMClient
from orchestrator.adapters.interfaces import InteractionChannel
from orchestrator.adapters.types import HtmlMockup, RawRequest, RequestBrief

if TYPE_CHECKING:
    from orchestrator.repo_init import RepoInitializer


# 公开常量：测试断言用。改这里也意味着改契约。
TECH_CONSTRAINT = (
    "你只能问业务问题，绝对禁止涉及任何技术细节"
    "（不要提组件名、文件名、CSS 属性、framework、状态管理、API 等）。"
    "你的目标是让业务员把业务效果说清楚。"
)

# 等 /init 完成的最长时间。超时则降级为纯凭截图 + 需求文本判断。
_INIT_WAIT_SECONDS = 120.0

# AGENTS.md 截断长度（喂 prompt 的 token 预算约束）
_REPO_DOC_MAX_CHARS = 6000


class BrainstormingSkill:
    """实现 InteractionSkill Protocol。"""

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_questions: int = 3,
        repo_initializer: "RepoInitializer | None" = None,
    ):
        self._llm = llm
        self._max_questions = max_questions
        self._repo_initializer = repo_initializer

    async def clarify(
        self, raw: RawRequest, channel: InteractionChannel
    ) -> RequestBrief:
        # 等 /init 就绪。失败/超时不阻塞 —— 进入降级模式（无 repo doc）。
        repo_doc = await self._load_repo_doc()

        plan = await self._plan(raw, repo_doc)
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

    # ── 内部 ─────────────────────────────────────────────────────────
    async def _load_repo_doc(self) -> str:
        if self._repo_initializer is None:
            return ""
        ok = await self._repo_initializer.wait_ready(timeout=_INIT_WAIT_SECONDS)
        if not ok:
            return ""  # 超时降级
        return self._repo_initializer.doc_content()[:_REPO_DOC_MAX_CHARS]

    async def _plan(self, raw: RawRequest, repo_doc: str) -> dict:
        prompt = self._build_plan_prompt(raw, repo_doc)
        try:
            text = await self._llm.complete_vision(prompt, raw.screenshot_b64)
        except Exception:
            return {"weight": "light", "questions": []}
        return _safe_parse_json(text)

    def _build_plan_prompt(self, raw: RawRequest, repo_doc: str) -> str:
        parts: list[str] = [TECH_CONSTRAINT, ""]

        if repo_doc:
            parts.append("=== 项目概览（基于此判断业务是否真有歧义） ===")
            parts.append(repo_doc)
            parts.append("=== 概览结束 ===")
            parts.append("")

        parts.append(
            "下面是业务员对一个 web 页面的截图（含他框选的区域），"
            "以及他用自然语言描述的需求。\n"
            "请按下面的 JSON 格式返回（不要任何额外文字）：\n"
            '{\n'
            '  "weight": "light" | "heavy",\n'
            '  "questions": ["业务问题 1", "..."],   // 仅 light 给\n'
            '  "variants": [{"id":"v1","title":"...","html":"<style+html>"}]  // 仅 heavy\n'
            '}\n\n'
            "**判定原则（严格）**：\n"
            "- **文案/颜色/单字段微调** → weight=light, questions=[]。"
            "  不要为了显得勤奋而提问。\n"
            "- **业务语义有真歧义** → weight=light, questions=[1 个最关键的]。\n"
            "  歧义判定：项目概览里有多个候选位置（例「订单页」可能指 /orders 列表"
            "  也可能指 /orders/:id 详情），或业务员明确遗漏关键决策（例「加搜索」但"
            "  没说按哪个字段搜）。**普通改动不算歧义。**\n"
            "- **信息架构变化** → weight=heavy, variants=2-3 套独立 HTML mockup"
            "  （每个含 inline style），作为方向锚点。\n\n"
            "**绝不能问的问题类型**：\n"
            "- 「用户是不是没有订单？」（业务员答不了）\n"
            "- 「这个按钮叫什么组件？」（技术细节）\n"
            "- 「是不是要加权限校验？」（实现层）\n"
            "- 你只能问业务员**作为客户**能答的：「你想看到啥样的效果？」\n\n"
            f"业务员的需求：{raw.request_text}\n"
            f"页面 URL：{raw.url}\n"
            f"框选坐标：{raw.box_coords}\n"
        )
        return "\n".join(parts)


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
