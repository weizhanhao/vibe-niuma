"""BrainstormingSkill —— LLM 自评式澄清：unknowns 列表驱动多轮提问。

设计原则（业务员反馈直接定的）：
- **不靠关键词补丁**：keyword precheck 永远穷举不完，业务员说话千变万化。
- **靠 LLM 自己证明它懂了**：每轮让 LLM 列出「还要知道哪些事 才能写代码」。
  unknowns 是空列表 ⇒ 真懂了 ⇒ done。unknowns 非空 ⇒ 必须再问。
- **不在乎几轮**：业务员的需求重要，token 不重要。轮数硬上限只是防 LLM 死循环
  的安全网，不是设计约束。业务员主动按「✓ 够了直接干」（STOP_CLARIFY_SENTINEL）
  随时退出。
- **两段式 LLM 调用**：
    1. 第一轮 vision 模型描述截图（业务员指什么）→ 缓存为 screen_context
    2. 每轮 text 模型读 screen_context + request + prev_answers → 出 unknowns
  vision 模型只调一次，省成本；判断逻辑放 text 模型，对长指令服从度更高。
- **heavy 路径不变**：信息架构变化（重新设计页面）走 HTML mockup 选择。

JSON 解析失败 → 兜底**绝不默认 done**，问一个开放问题让业务员补充。
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
_INIT_WAIT_SECONDS = 30.0

# AGENTS.md 截断长度（喂 prompt 的 token 预算约束）
_REPO_DOC_MAX_CHARS = 6000

# 业务员主动「✓ 够了直接干」按钮发的特殊 answer 字符串。channel.ask 收到这个
# 立刻 break loop 进 located（业务员决定权 > LLM 想多问）。
STOP_CLARIFY_SENTINEL = "__STOP_CLARIFY__"

# 安全上限：LLM 进入 unknowns 永不收敛的退化状态时强制 break。业务员正常
# 应该用 STOP_CLARIFY_SENTINEL 提前结束；这只是死循环保险丝。
_HARD_ROUND_CAP = 12

# 截图描述的字符上限（喂下游 text 模型时控制 prompt 长度）
_SCREEN_CONTEXT_MAX_CHARS = 600

# describe screen 用的 vision prompt：必须列**实际可见的 UI 控件类型**，
# 不能凭空说「应该是下拉框」之类。这条 prompt 的输出后续 text 模型会当
# 「当前画面真相」用，所以**宁可写少也不能瞎补**。
_DESCRIBE_SCREEN_PROMPT = (
    "看这张网页截图，**只列你眼睛真看到的 UI 元素**（不要推断、不要补「应该有」）。\n"
    "\n"
    "格式：\n"
    "- 主体：是什么页面（订单列表 / 商品详情 / 表单 / dashboard ...）\n"
    "- 业务员**很可能在指向**的那个区块：列出**具体控件类型**\n"
    "  * 区分清楚：下拉 select / 按钮组 button group / chip 标签 / radio / "
    "checkbox / tab / 输入框 / 表格行...\n"
    "  * 含主要文本/标签（比如「按钮：已支付 / 待支付 / 已发货 / 已取消」）\n"
    "- 业务员**没框选**时也要尽量定位 —— 找页面里**视觉上最突出的可操作区**\n"
    "\n"
    "**严禁**：用「可能是」「应该是」「之前是」「这种页面通常」开头。\n"
    "**严禁**：根据经验补全画面里**没真看到**的元素。\n"
    "总长 120 字以内。"
)


# 主 prompt：让 LLM 列「还要知道哪些事才能写代码」+ 一次问多题 + 标推荐。
UNKNOWNS_PROMPT_TEMPLATE = """\
你是 doskill 的需求澄清助手。业务员对一个网页提了改造需求，你要 **用代码精确实现它**。
在你能写下第一行代码之前，把所有 **业务层未知** 列出来。

# 核心原则

**宁可多问也别瞎改。** 业务员凭感觉说话（「好看点」「换一种」「不喜欢」），
代码却必须精确（哪个 UI 组件、哪些值、保留哪些选项）。每条「不知道」都要变成
一个具体问题。

**严禁假设 UI 不存在或形态错误。** 当前画面的 UI 真相**只能看 screen_context**，
不能凭 repo_doc / 经验补「应该有」。例：screen_context 写「状态筛选是 4 个 chip
按钮」，**绝对不能问「是不是那个下拉框」**，那是错的；应该直接问「这一排 chip
按钮想换成什么」。在「让业务员确认改哪个元素」这类问题上要**复述 screen_context
里的真实控件类型**。

**判 unknowns 为空（done）的硬标准**：业务员的话能直接对应一段具体代码改动 ——
每个字段名 / 取值 / 组件类型 / 数据流都明确。**如果你脑子里要「猜」任何一处 → 那处
就是 unknown。**「我觉得用 segmented 控件比较合适」「我猜业务员想要 ...」都是错的。

技术细节不算 unknown：「改哪个文件」「用 useState 还是 useReducer」「class 还是 css
module」「动画时长」「文件路径」—— 这些是技术决策，不是业务问题，**不要问，不要列**。

**多题打包**：如果 unknowns 里有 **2-4 个互相独立** 的问题（决定一项不会影响另一项），
**打包一次性问**（业务员一次提交）。如果某项的答案会决定后续要问什么（依赖），
**只问那一项**，后面的下一轮再问。

**推荐项**：每题里挑出**你最推荐**的选项，用 `recommended` 字段标出来（写完整 option
文本，不是 index）。业务员看到 (推荐) 标签会更容易决定，不知道该选什么时也有锚点。
**没明显推荐就留空**，别强行推荐。

# 输入

## 截图里看到的元素（**这是当前画面真相，优先级最高**）
{screen_context}

## 项目知识（业务/数据规则参考；UI 状态可能滞后于截图，**截图为准**）
{repo_doc}

## 业务员原始需求
{request}

## 已经问过的轮次（最新在最下）
{prev_qa}

# 输出格式（严格 JSON，不要任何额外文字）

```
{{
  "weight": "light",
  "unknowns": ["...", "..."],
  "questions": [
    {{
      "question": "想换成什么样的 UI 控件？",
      "options": ["横排按钮组", "顶部 tab", "segmented 控件（iOS 风）", "我自己描述"],
      "multi": false,
      "recommended": "segmented 控件（iOS 风）"
    }},
    {{
      "question": "要保留哪些状态值？(可多选)",
      "options": ["全部", "已支付", "待支付", "已取消", "已退款"],
      "multi": true,
      "recommended": null
    }}
  ]
}}
```

- `weight`: 一般是 `"light"`；信息架构大改用 `"heavy"` 走 `variants` 路径（同前）。
- `unknowns`: 具体到字段/元素/取值的未知点。**空数组 ⇒ done，直接写代码。**
- `questions`: unknowns 非空时必填，**1-4 个**。
  - 每个 question 自带 `options`（2-5 个）+ `multi`（true=多选 / false=单选）+ `recommended`（可选）
  - **`options` 最后一个固定「我自己描述」**（multi=true 时业务员可勾任意多个 + 自己描述）
  - 当且仅当 unknowns 全独立时一次问多个；任何一个的答案会改变后面问什么 → 只问第一个
- `recommended` 必须是 options 数组里某个完整字符串（不是 index）；不强推则填 null

# unknowns 的好例子

- "业务员说『不喜欢下拉框』但没说换成什么 UI 控件"
- "业务员没说要保留哪些状态值"
- "业务员说『好看点』但没指出是配色/布局/字号问题"

# 一次问多题 vs 只问一题

✓ **可打包**（独立）：业务员说「改下拉框」→ 同时问「换成什么控件 / 保留哪些状态值」
  —— 两个答案互不影响。
✗ **不可打包**（有依赖）：业务员说「让首页好看点」→ 不能同时问「配色用什么 / 主色调 RGB」
  —— 先确认是配色问题，才有必要问 RGB。

# question / options 形式约束

- options 必须是业务员能直接选的**具体方案**（写出关键参数），不是「方向 A / 方向 B」这种空泛词
- 不能问技术（组件名、文件、API、CSS 属性）
- 不能问业务员答不了的（「用户是不是没下过单」「这字段什么时候添加的」）
"""


class BrainstormingSkill:
    """实现 InteractionSkill Protocol。"""

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_questions: int = 3,  # 历史参数，新设计不用；保留兼容签名
        repo_initializer: "RepoInitializer | None" = None,
    ):
        self._llm = llm
        self._max_questions = max_questions
        self._repo_initializer = repo_initializer
        # 单次 clarify 内缓存的截图描述（vision 调用结果）
        self._screen_context_cache: dict[str, str] = {}

    async def clarify(
        self, raw: RawRequest, channel: InteractionChannel
    ) -> RequestBrief:
        """多轮 unknowns 驱动澄清。

        每轮：
        - 调 LLM 出 plan = {unknowns, question, options}
        - unknowns 空 → break
        - 否则 channel.ask(question, options)
        - 业务员答 STOP_CLARIFY_SENTINEL → break
        - append clarifications，进下一轮
        - 安全网：_HARD_ROUND_CAP 轮防 LLM 死循环
        """
        log = getattr(channel, "log", None)
        if callable(log):
            await log("▸ 加载 AGENTS.md（项目知识）...")
        repo_doc = await self._load_repo_doc()
        if callable(log):
            if repo_doc:
                await log(f"✓ AGENTS.md 已加载 {len(repo_doc)} 字符")
            else:
                await log("⚠ AGENTS.md 不可用，纯凭截图 + 描述判")

        # 第一轮的 vision 描述（缓存复用，多轮不重复调）
        if callable(log):
            await log("▸ 视觉模型描述截图里的元素...")
        screen_context = await self._describe_screen(raw, log=log)

        if callable(log):
            await log("▸ 文本模型评估「需求清楚了吗」...")
        plan = await self._plan(
            raw, repo_doc, screen_context=screen_context,
            channel=channel, prev_answers=[],
        )
        clarifications: list[dict] = []
        selected_mockup: HtmlMockup | None = None

        # heavy 路径：variants 选择，不进 unknowns loop
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

        # light 路径：unknowns 驱动多轮，每轮 LLM 可一次问 1-4 题（独立的打包问）
        current_plan = plan
        round_i = 0
        while True:
            unknowns = current_plan.get("unknowns")
            if isinstance(unknowns, list) and len(unknowns) == 0:
                if callable(log):
                    await log("✓ LLM 报告 unknowns=[] —— 需求已清晰")
                break

            questions = current_plan.get("questions") or []
            if not questions:
                # 没题又 unknowns 非空 → 退化场景，break 防卡死
                if callable(log):
                    await log("⚠ LLM 没给 questions 也没说 done，break")
                break

            if isinstance(unknowns, list) and unknowns and callable(log):
                await log(f"还有 {len(unknowns)} 个未知项：")
                for u in unknowns[:5]:
                    await log(f"  · {u}")

            # 一题走老的 ask（保留单题路径，UI 不变）；多题走 present_form
            if len(questions) == 1:
                q = questions[0]
                # 把 recommended 透传给 channel：扩展用 dict 形态的 options 包载荷
                answer = await self._ask_single(channel, q)
                if answer is None:
                    break
                answer = answer.strip()
                if answer == STOP_CLARIFY_SENTINEL:
                    if callable(log):
                        await log("✓ 业务员主动结束澄清，进入定位...")
                    break
                if answer:
                    clarifications.append({
                        "question": q["question"], "answer": answer,
                    })
            else:
                # 多题表单：一次问，等业务员一次提交
                form_answers = await self._present_form(channel, questions)
                if form_answers is None:
                    break
                if form_answers.get("__stop__"):
                    if callable(log):
                        await log("✓ 业务员主动结束澄清，进入定位...")
                    break
                for q in questions:
                    a = form_answers.get(q["question"], "")
                    if a:
                        clarifications.append({"question": q["question"], "answer": a})

            round_i += 1
            if round_i >= _HARD_ROUND_CAP:
                if callable(log):
                    await log(f"⚠ 已达硬上限 {_HARD_ROUND_CAP} 轮（LLM 可能死循环），break")
                break

            current_plan = await self._plan(
                raw, repo_doc, screen_context=screen_context,
                channel=channel, prev_answers=clarifications,
            )

        return RequestBrief(
            original_text=raw.request_text,
            clarifications=clarifications,
            selected_mockup=selected_mockup,
        )

    # ── 内部 ─────────────────────────────────────────────────────────
    async def _ask_single(
        self, channel: InteractionChannel, q: dict,
    ) -> str | None:
        """单题路径：透传 recommended 给 channel（如它支持），fall back 普通 ask。

        recommended 走 options 列表外的 kwarg；老 channel 不识别就直接走基本路径。
        """
        question = q["question"]
        options = q.get("options") or None
        recommended = q.get("recommended")
        # 如果 channel 有 ask_rich（接 recommended），优先用；否则降级
        ask_rich = getattr(channel, "ask_rich", None)
        if callable(ask_rich):
            return await ask_rich(question, options, recommended=recommended)
        return await channel.ask(question, options)

    async def _present_form(
        self, channel: InteractionChannel, questions: list[dict],
    ) -> dict | None:
        """多题路径：调 channel.present_form；老 channel 没此方法时降级串行 ask。"""
        present_form = getattr(channel, "present_form", None)
        if callable(present_form):
            return await present_form(questions)
        # 降级：一题一题问；任何一答 STOP 立刻退出
        answers: dict = {}
        for q in questions:
            a = await self._ask_single(channel, q)
            if a is None:
                return None
            a = a.strip()
            if a == STOP_CLARIFY_SENTINEL:
                return {"__stop__": True}
            answers[q["question"]] = a
        return answers

    async def _load_repo_doc(self) -> str:
        if self._repo_initializer is None:
            return ""
        ok = await self._repo_initializer.wait_ready(timeout=_INIT_WAIT_SECONDS)
        if not ok:
            return ""
        return self._repo_initializer.doc_content()[:_REPO_DOC_MAX_CHARS]

    async def _describe_screen(
        self, raw: RawRequest, *, log=None,
    ) -> str:
        """第一轮 vision 调用：让 qwen-vl-plus 描述截图里能看到什么。

        缓存于 self._screen_context_cache —— 同一截图 b64 多轮 clarify 复用。
        失败/无截图 → 返空串，下游 text 模型仍能基于需求 + 历史判断。
        """
        key = (raw.screenshot_b64 or "")[:64]
        if key in self._screen_context_cache:
            return self._screen_context_cache[key]

        images = raw.images()
        if not images and not raw.screenshot_b64:
            self._screen_context_cache[key] = ""
            return ""

        try:
            if len(images) > 1:
                text = await self._llm.complete_vision_multi(
                    _DESCRIBE_SCREEN_PROMPT, images,
                )
            else:
                text = await self._llm.complete_vision(
                    _DESCRIBE_SCREEN_PROMPT, raw.screenshot_b64,
                )
        except Exception:  # noqa: BLE001
            if callable(log):
                try:
                    await log("⚠ 视觉描述失败，brainstorm 将无截图上下文")
                except Exception:
                    pass
            self._screen_context_cache[key] = ""
            return ""

        cleaned = (text or "").strip()[:_SCREEN_CONTEXT_MAX_CHARS]
        self._screen_context_cache[key] = cleaned
        if callable(log) and cleaned:
            try:
                await log(f"✓ 截图描述：{cleaned[:120]}{'...' if len(cleaned) > 120 else ''}")
            except Exception:
                pass
        return cleaned

    async def _plan(
        self, raw: RawRequest, repo_doc: str, *,
        screen_context: str = "",
        channel: InteractionChannel | None = None,
        prev_answers: list[dict] | None = None,
    ) -> dict:
        """每轮判断：返 {weight, unknowns, questions} 或 heavy 的 variants。

        策略：
        - 主路径用 text-only 模型（对长指令服从度高），偶发失败重试 1 次
        - 重试也挂 → 仅在 raw 真有截图时降级到 vision（避免 qwen-vl-plus
          收到空 base64 返 400 又错一次）
        - 两层都挂 → fallback_open_question，**带上当前 prev_answers 的最后
          一题让业务员重答**，不发那个完全无用的「再具体描述」泛问
        """
        prev_answers = prev_answers or []
        log = getattr(channel, "log", None) if channel is not None else None

        prompt = self._build_unknowns_prompt(
            raw, repo_doc, screen_context=screen_context,
            prev_answers=prev_answers,
        )

        # 主路径：text-only stream（deepseek-v4-pro）。
        # 用 stream 而不是 complete()：长 prompt（round 2+ 累积 prev_answers）
        # 经常 >60s 完成，httpx 一次性 timeout 会切断；stream 每 token 续约
        # 连接 + 给业务员 cursor-like 体验。失败重试 1 次防偶发抖动。
        last_text_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                # token 流不直接 publish（太碎），累一行再推；与 _describe_screen
                # 共享同款 buf flush 节奏，扩展 UI 不会卡。
                buf: list[str] = []

                async def _on_token(tok: str) -> None:
                    if not callable(log):
                        return
                    buf.append(tok)
                    joined = "".join(buf)
                    if "\n" in tok or len(joined) >= 48:
                        try:
                            await log(joined.replace("\n", " ").strip())
                        except Exception:
                            pass
                        buf.clear()

                if callable(log):
                    text = await self._llm.complete_stream(prompt, on_token=_on_token)
                else:
                    # 没 channel.log 就退非流式（测试/直接 API 调用场景）
                    text = await self._llm.complete(prompt)
                parsed = _parse_unknowns(text)
                if callable(log):
                    n = len(parsed.get("unknowns") or [])
                    await log(f"✓ LLM 评估完成 unknowns={n}")
                return parsed
            except Exception as exc:  # noqa: BLE001
                last_text_exc = exc
                if callable(log):
                    try:
                        await log(
                            f"⚠ text LLM 第 {attempt} 次失败：{type(exc).__name__}: {exc}",
                        )
                    except Exception:
                        pass

        # 降级 vision：仅在真有截图时尝试。没截图传空 base64 会被 dashscope
        # 直接 400，反而绕开了我们能给业务员的友好兜底。
        has_screenshot = bool(raw.screenshot_b64) or bool(raw.images())
        if has_screenshot:
            try:
                if len(raw.images()) > 1:
                    text = await self._llm.complete_vision_multi(prompt, raw.images())
                else:
                    text = await self._llm.complete_vision(prompt, raw.screenshot_b64)
                return _parse_unknowns(text)
            except Exception:  # noqa: BLE001
                if callable(log):
                    try:
                        await log("⚠ vision 兜底也失败，进开放问题")
                    except Exception:
                        pass

        # 最终兜底：友好提示 LLM 错了 + 沿用上一轮 unknowns（如果有）让业务员重答
        if callable(log):
            try:
                await log(f"⚠ LLM 完全不可用，进兜底（last={last_text_exc}）")
            except Exception:
                pass
        return _fallback_open_question(prev_answers=prev_answers)

    def _build_unknowns_prompt(
        self, raw: RawRequest, repo_doc: str, *,
        screen_context: str = "",
        prev_answers: list[dict] | None = None,
    ) -> str:
        """填 UNKNOWNS_PROMPT_TEMPLATE 的占位符。"""
        prev_qa_lines: list[str] = []
        for i, qa in enumerate(prev_answers or [], 1):
            q = (qa.get("question") or "").strip()
            a = (qa.get("answer") or "").strip()
            if q or a:
                prev_qa_lines.append(f"轮 {i}:")
                prev_qa_lines.append(f"  Q: {q}")
                prev_qa_lines.append(f"  A: {a}")
        prev_qa = "\n".join(prev_qa_lines) if prev_qa_lines else "（暂无）"

        body = UNKNOWNS_PROMPT_TEMPLATE.format(
            screen_context=screen_context or "（无截图描述）",
            repo_doc=repo_doc or "（无）",
            request=raw.request_text or "（空）",
            prev_qa=prev_qa,
        )
        # TECH_CONSTRAINT 作为强约束顶部声明 —— 老 contract 测试也断言其存在
        return f"{TECH_CONSTRAINT}\n\n{body}"

    # 历史别名 —— 旧 contract 测试 import 这个名字
    def _build_plan_prompt(
        self, raw: RawRequest, repo_doc: str, **kwargs,
    ) -> str:
        return self._build_unknowns_prompt(raw, repo_doc, **kwargs)


# ── JSON 解析（绝不默认 done） ─────────────────────────────────────
_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _fallback_open_question(prev_answers: list[dict] | None = None) -> dict:
    """LLM 出错时绝不默认 done —— 问一个能继续推进的问题。

    设计：
    - 标题诚实说 AI 这次没判明白，不假装在继续 brainstorm
    - 只有「我自己描述」一个选项 + 「✓ 够了直接干」（业务员答完表单还撞到
      LLM 错时，最不该的是让他再描述一长串需求）
    """
    last_q = ""
    if prev_answers:
        last_q = (prev_answers[-1].get("question") or "").strip()
    suffix = f"\n\n（上一题是：「{last_q}」）" if last_q else ""
    return {
        "weight": "light",
        "unknowns": ["LLM 临时不可用 — 让业务员补一条直接描述"],
        "questions": [{
            "question": "⚠ AI 暂时没能判断需求，请直接用一两句话说你想要什么效果（"
                        "什么元素、想换成什么样、要保留什么）。如果上次答案已经够"
                        "用，可以点「✓ 够了直接干」让 AI 按现有理解开始。" + suffix,
            "options": ["我自己描述"],
            "multi": False,
            "recommended": None,
        }],
    }


def _normalize_question(q: dict) -> dict:
    """把 LLM 返回的一个 question 字典规整成内部稳定 schema。"""
    options = [str(o) for o in (q.get("options") or []) if str(o).strip()]
    rec_raw = q.get("recommended")
    # recommended 必须命中 options 之一才有效，避免脏数据导致 UI 高亮错位
    recommended = str(rec_raw) if rec_raw and str(rec_raw) in options else None
    return {
        "question": str(q.get("question") or "").strip(),
        "options": options,
        "multi": bool(q.get("multi") or False),
        "recommended": recommended,
    }


def _parse_unknowns(text: str) -> dict:
    """解析 LLM 返 {weight, unknowns, questions[]}（兼容老 {question, options}）。

    解析失败 → 兜底**开放问题**（不 default done）。
    兼容老 LLM 返 {done: true/false}：done=true 视为 unknowns=[]。
    兼容 heavy: {weight:"heavy", variants:[...]}。
    """
    if not text:
        return _fallback_open_question()
    m = _JSON_BLOCK.search(text)
    if not m:
        return _fallback_open_question()
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return _fallback_open_question()
    if not isinstance(data, dict):
        return _fallback_open_question()

    # heavy 路径透传
    if data.get("weight") == "heavy" and isinstance(data.get("variants"), list):
        return data

    # 优先用 unknowns 字段；老 LLM 还在用 done 兼容一下
    unknowns_raw = data.get("unknowns")
    if isinstance(unknowns_raw, list):
        unknowns = [str(u) for u in unknowns_raw if str(u).strip()]
    elif data.get("done") is True:
        unknowns = []
    elif data.get("done") is False:
        unknowns = ["（LLM 未列具体未知项但报告未 done）"]
    else:
        return _fallback_open_question()

    # questions: 新版 LLM 返列表；老版返单 question + options 平铺
    questions_raw = data.get("questions")
    questions: list[dict] = []
    if isinstance(questions_raw, list) and questions_raw:
        for q in questions_raw[:4]:
            if isinstance(q, dict):
                nq = _normalize_question(q)
                if nq["question"]:
                    questions.append(nq)
    elif data.get("question"):
        # 老 schema: 单题 + 平铺 options
        questions.append(_normalize_question({
            "question": data.get("question"),
            "options": data.get("options"),
            "multi": data.get("multi"),
            "recommended": data.get("recommended"),
        }))

    return {
        "weight": "light",
        "unknowns": unknowns,
        "questions": questions,
    }


# 历史 API 别名 —— 老测试 import _safe_parse_json 仍能拿到等价行为
def _safe_parse_json(text: str) -> dict:
    return _parse_unknowns(text)
