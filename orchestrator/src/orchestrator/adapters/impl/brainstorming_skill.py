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

# describe screen 用的 vision prompt：让模型只描述「业务员可能指向的元素」，
# 不要长篇大论整个页面。
_DESCRIBE_SCREEN_PROMPT = (
    "用 80 字以内描述这张网页截图里业务员**可能在指向**的元素：\n"
    "- 是什么（表格 / 卡片 / 下拉 / 按钮 / 输入框 / 区块 ...）\n"
    "- 周围相关元素\n"
    "- 当前状态（有没有数据、是默认态还是选中态）\n"
    "只描述客观看到的，不要猜业务意图。"
)


# 主 prompt：让 LLM 列「还要知道哪些事才能写代码」。
UNKNOWNS_PROMPT_TEMPLATE = """\
你是 doskill 的需求澄清助手。业务员对一个网页提了改造需求，你要 **用代码精确实现它**。
在你能写下第一行代码之前，把所有 **业务层未知** 列出来。

# 核心原则

**宁可多问也别瞎改。** 业务员凭感觉说话（「好看点」「换一种」「不喜欢」），
代码却必须精确（哪个 UI 组件、哪些值、保留哪些选项）。每条「不知道」都要变成
一个具体问题。

**判 unknowns 为空（done）的硬标准**：业务员的话能直接对应一段具体代码改动 ——
每个字段名 / 取值 / 组件类型 / 数据流都明确。**如果你脑子里要「猜」任何一处 → 那处
就是 unknown。**「我觉得用 segmented 控件比较合适」「我猜业务员想要 ...」都是错的。

技术细节不算 unknown：「改哪个文件」「用 useState 还是 useReducer」「class 还是 css
module」「动画时长」「文件路径」—— 这些是技术决策，不是业务问题，**不要问，不要列**。

# 输入

## 截图里看到的元素
{screen_context}

## 项目知识（如有）
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
  "question": "下一个最该问的业务问题",
  "options": ["选项1（具体方案）", "选项2", "选项3", "我自己描述"]
}}
```

- `weight`: 一般是 `"light"`；如果业务员要的是**重新设计整个页面**（信息架构大改），
  用 `"heavy"` 并给 `variants` 字段：`[{{"id":"v1","title":"...","html":"<完整 HTML 草稿>"}}]`，
  2-3 套独立 mockup，不再走 unknowns 路径。
- `unknowns`: 列具体到字段/元素/取值的未知点。**空数组 ⇒ done，直接写代码。**
- `question`: unknowns 非空时必填。问下一个最重要的那一个。
- `options`: unknowns 非空时必填 2-4 个具体可选项，**最后一个固定「我自己描述」**。

# unknowns 的好例子（具体、可问）

- "业务员说『不喜欢下拉框』但没说换成什么 UI 控件（按钮组 / tab / segmented / chip）"
- "业务员没说要保留哪些状态值（全部/已支付/待支付？按订单流程分？）"
- "业务员说『好看点』但没指出是配色/布局/字号问题"
- "业务员说『加个图标』但没说放哪个位置（左边/右边/独立按钮）"

# unknowns 为空的正确例子（清晰、可写）

- 「按钮背景色改成 #1890ff」→ 元素=按钮背景 取值=#1890ff，**没有 unknown**
- 「placeholder 改成『按客户名搜索』」→ 元素=placeholder 取值=具体文本，**没有 unknown**
- 「字号 14px 改成 16px」→ 元素+取值都有，**没有 unknown**
- 「下拉框换成 segmented 控件，状态保留 全部/已支付/待支付」→ 控件+数据全有，**没有 unknown**

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

        # light 路径：unknowns 驱动多轮
        current_plan = plan
        round_i = 0
        while True:
            unknowns = current_plan.get("unknowns")
            if isinstance(unknowns, list) and len(unknowns) == 0:
                if callable(log):
                    await log("✓ LLM 报告 unknowns=[] —— 需求已清晰")
                break

            question = (current_plan.get("question") or "").strip()
            if not question:
                # 没 question 又 unknowns 非空 → 退化场景，break 防卡死
                if callable(log):
                    await log("⚠ LLM 没给 question 也没说 done，break")
                break

            if isinstance(unknowns, list) and unknowns and callable(log):
                await log(f"还有 {len(unknowns)} 个未知项：")
                for u in unknowns[:5]:
                    await log(f"  · {u}")

            options = current_plan.get("options") or []
            if not isinstance(options, list):
                options = []
            options = [str(o) for o in options if o]
            answer = await channel.ask(question, options or None)
            if answer is None:
                break
            answer = answer.strip()
            if answer == STOP_CLARIFY_SENTINEL:
                if callable(log):
                    await log("✓ 业务员主动结束澄清，进入定位...")
                break
            if answer:
                clarifications.append({"question": question, "answer": answer})

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
        """每轮判断：返 {weight, unknowns, question, options} 或 heavy 的 variants。

        优先用 text-only 模型（complete）—— 对长指令服从度高。失败时降级 vision。
        """
        prev_answers = prev_answers or []
        log = getattr(channel, "log", None) if channel is not None else None

        prompt = self._build_unknowns_prompt(
            raw, repo_doc, screen_context=screen_context,
            prev_answers=prev_answers,
        )

        # 主路径：text-only model（deepseek-v4-pro，跟 dev_runner 同款）
        try:
            text = await self._llm.complete(prompt)
            parsed = _parse_unknowns(text)
            if callable(log):
                n = len(parsed.get("unknowns") or [])
                await log(f"✓ LLM 评估完成 unknowns={n}")
            return parsed
        except Exception as exc:  # noqa: BLE001
            if callable(log):
                try:
                    await log(f"⚠ text LLM 调用失败：{exc}，尝试 vision 兜底...")
                except Exception:
                    pass

        # 降级路径：vision model（也许是限速 / API 临时挂）
        try:
            if len(raw.images()) > 1:
                text = await self._llm.complete_vision_multi(prompt, raw.images())
            else:
                text = await self._llm.complete_vision(prompt, raw.screenshot_b64)
            return _parse_unknowns(text)
        except Exception:  # noqa: BLE001
            if callable(log):
                try:
                    await log("⚠ vision 也失败，进开放兜底问题（不 default done）")
                except Exception:
                    pass
            return _fallback_open_question()

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


def _fallback_open_question() -> dict:
    """LLM 出错时绝不默认 done —— 问一个开放问题逼业务员补细节。"""
    return {
        "weight": "light",
        "unknowns": ["LLM 解析失败/服务不可用 — 需要业务员补充细节"],
        "question": "再具体描述一下你想要的效果？比如换成什么样的 UI、要保留哪些功能。",
        "options": ["我自己描述"],
    }


def _parse_unknowns(text: str) -> dict:
    """解析 LLM 返的 {weight, unknowns, question, options}。

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
        # 老 LLM 没列 unknowns 但说没 done → 给个占位 unknown 防 default done
        unknowns = ["（LLM 未列具体未知项但报告未 done）"]
    else:
        # 没说 done 也没 unknowns → 兜底开放问题
        return _fallback_open_question()

    return {
        "weight": "light",
        "unknowns": unknowns,
        "question": str(data.get("question") or "").strip(),
        "options": [str(o) for o in (data.get("options") or []) if str(o).strip()],
    }


# 历史 API 别名 —— 老测试 import _safe_parse_json 仍能拿到等价行为
def _safe_parse_json(text: str) -> dict:
    return _parse_unknowns(text)
