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
# 120s 太长 —— 业务员会以为卡死。30s 内不就绪就走降级路径（无 AGENTS.md），
# init 失败时 wait_ready 立即 False 也不会等满。
_INIT_WAIT_SECONDS = 30.0

# AGENTS.md 截断长度（喂 prompt 的 token 预算约束）
_REPO_DOC_MAX_CHARS = 6000

# Plan 10 Task 8：真多轮澄清。LLM 每轮重新评估「done? 下一题是什么? 选项」，
# 软上限 8 轮防贪心（业务员被狂问 8 题已经体感很差，再多就强制 break）。
MAX_SOFT_ROUNDS = 8

# 业务员主动「✓ 够了直接干」按钮发的特殊 answer 字符串。channel.ask 收到这个
# 立刻 break loop 进 located（业务员决定权 > LLM 想多问）。
STOP_CLARIFY_SENTINEL = "__STOP_CLARIFY__"


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
        """Plan 10 Task 8：真多轮澄清 loop。

        - 第一轮 plan：判 weight (light / heavy)
        - heavy → present_variants 让业务员选方案（单次，不进 loop）
        - light → for round_i in range(MAX_SOFT_ROUNDS):
            - plan 返 done=True → break
            - 拿 question + options，channel.ask(q, options)
            - answer == STOP_CLARIFY_SENTINEL → break
            - 否则 append clarification，进下一轮
        """
        log = getattr(channel, "log", None)
        if callable(log):
            await log("▸ 加载 AGENTS.md（项目知识）...")
        repo_doc = await self._load_repo_doc()
        if callable(log):
            if repo_doc:
                await log(f"✓ AGENTS.md 已加载 {len(repo_doc)} 字符")
            else:
                await log("⚠ AGENTS.md 不可用，降级为纯截图判断")

        if callable(log):
            await log("▸ 调用视觉模型评估意图...")
        plan = await self._plan(raw, repo_doc, channel=channel, prev_answers=[])
        clarifications: list[dict] = []
        selected_mockup: HtmlMockup | None = None

        # heavy 路径：variants 选择，不进 loop
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
            return RequestBrief(
                original_text=raw.request_text,
                clarifications=clarifications,
                selected_mockup=selected_mockup,
            )

        # light 路径：真多轮 loop
        current_plan = plan
        for round_i in range(MAX_SOFT_ROUNDS):
            if current_plan.get("done") is True:
                break
            question = (current_plan.get("question") or "").strip()
            if not question:
                # LLM 没给 question 也没说 done → 视为 done
                break

            options = current_plan.get("options") or []
            if not isinstance(options, list):
                options = []
            options = [str(o) for o in options if o]
            answer = await channel.ask(question, options or None)
            if answer is None:
                break
            answer = answer.strip()
            if answer == STOP_CLARIFY_SENTINEL:
                # 业务员主动按「✓ 够了直接干」
                if callable(log):
                    await log("✓ 业务员主动结束澄清，进入定位...")
                break
            if answer and answer != "跳过":
                clarifications.append({"question": question, "answer": answer})

            # 还没到上限就再问 LLM 评估下一题
            if round_i + 1 >= MAX_SOFT_ROUNDS:
                if callable(log):
                    await log(f"⚠ 已达软上限 {MAX_SOFT_ROUNDS} 轮，强制进入定位...")
                break
            current_plan = await self._plan(
                raw, repo_doc, channel=channel, prev_answers=clarifications,
            )

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

    async def _plan(
        self, raw: RawRequest, repo_doc: str, *,
        channel: InteractionChannel | None = None,
        prev_answers: list[dict] | None = None,
    ) -> dict:
        prompt = self._build_plan_prompt(raw, repo_doc, prev_answers=prev_answers or [])
        log = getattr(channel, "log", None) if channel is not None else None
        # 优先流式（cursor-like 体验）：每个 token chunk 经 channel.log 回灌 SSE。
        # 上游不支持 stream 或网络出错时降级到非流式 complete_vision，最终再失败才退降级 plan。
        if callable(log):
            try:
                buf: list[str] = []

                async def _on_token(tok: str) -> None:
                    # 累一行再 publish：避免 single-char publish 把扩展端冲爆，
                    # 但太长就 flush，保持 cursor-like 感觉。
                    buf.append(tok)
                    joined = "".join(buf)
                    if "\n" in tok or len(joined) >= 32:
                        await log(joined.replace("\n", " ").strip())
                        buf.clear()

                text = await self._llm.complete_vision_stream(
                    prompt, raw.screenshot_b64, on_token=_on_token,
                )
                # flush 残余
                tail = "".join(buf).strip()
                if tail:
                    try:
                        await log(tail)
                    except Exception:
                        pass
                return _safe_parse_json(text)
            except Exception:
                try:
                    await log("⚠ 流式调用失败，回退非流式...")
                except Exception:
                    pass

        try:
            text = await self._llm.complete_vision(prompt, raw.screenshot_b64)
        except Exception:
            return {"weight": "light", "questions": []}
        return _safe_parse_json(text)

    def _build_plan_prompt(
        self, raw: RawRequest, repo_doc: str,
        *, prev_answers: list[dict] | None = None,
    ) -> str:
        """Plan 10 Task 8：每轮拿 prev_answers，让 LLM 累计判断「还问不问」。

        返回 JSON：
          - heavy 路径：{"weight":"heavy","variants":[...]}
          - light 路径 done：{"weight":"light","done":true}
          - light 路径继续：{"weight":"light","done":false,"question":"...","options":[...]}

        强制 options 2-4 个 + 最后一个必须是「我自己描述」。
        """
        parts: list[str] = [TECH_CONSTRAINT, ""]

        if repo_doc:
            parts.append("=== 项目概览（基于此判断业务是否真有歧义） ===")
            parts.append(repo_doc)
            parts.append("=== 概览结束 ===")
            parts.append("")

        prev_block = ""
        if prev_answers:
            prev_lines = ["", "=== 业务员已经回答过的问题 ==="]
            for i, qa in enumerate(prev_answers, 1):
                prev_lines.append(f"{i}. Q: {qa.get('question', '')}")
                prev_lines.append(f"   A: {qa.get('answer', '')}")
            prev_lines.append("=== 已答结束 ===")
            prev_block = "\n".join(prev_lines) + "\n"

        parts.append(
            "下面是业务员对一个 web 页面的截图（含他框选的区域），"
            "以及他用自然语言描述的需求。\n"
            + prev_block +
            "请按下面的 JSON 格式返回（不要任何额外文字）：\n"
            '{\n'
            '  "weight": "light" | "heavy",\n'
            '  "done": true | false,                          // light 必填；true=不再问了，可以开干\n'
            '  "question": "下一个要问的业务问题",                  // light + done=false 必填\n'
            '  "options": ["选项 1","选项 2","选项 3","我自己描述"],  // light + done=false 必填，2-4 个，最后一个固定「我自己描述」\n'
            '  "variants": [{"id":"v1","title":"...","html":"<style+html>"}]  // 仅 heavy\n'
            '}\n\n'
            "**判定原则（严格）**：\n"
            "- **文案/颜色/单字段微调** → weight=light, done=true，**不问任何问题**。\n"
            "- **业务语义有真歧义** → weight=light, done=false, 给 1 个最关键的问题 + 2-4 个 options。\n"
            "- **每答完一轮你再判断**：如果已答信息足够开工 → done=true；还有真歧义 → 再给一个 question。\n"
            "- **信息架构变化** → weight=heavy, variants=2-3 套独立 HTML mockup。\n\n"
            "**问题的形式**：\n"
            "- options 必须是业务员能直接选的具体方案（不是空泛描述）\n"
            "- options 最后一个**必须固定**是「我自己描述」，让业务员能自定义答\n"
            "- 不能问技术细节（组件名、API、CSS 属性等）\n"
            "- 不能问业务员答不了的（「用户是不是没有订单？」）\n\n"
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
