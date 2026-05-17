"""Plan 10 Task 3: LLM-based intent classifier。

业务员说完一句话，pipeline 要判这条 message 走哪条路径：
- new_cr：完整 pipeline（clarify → locate → code → build → preview）
- refine_cr：复用上一 CR 的 branch + 同 preview 容器刷新
- chat_only：纯 LLM 回复，不进 docker / 不切 branch / 不耗 quota

设计要点（spec §Architecture）：
- 用 LLM 判（不是结构化规则）。业务员心智「AI 知道我的项目，截图是补充」，
  附件**不参与决策**。LLM 拿：最近 6 条 chat history + repo doc 摘要 +
  新消息 + 上一 CR 状态。
- 返 `{mode, confidence, reason}`；confidence < 0.6 标 is_unsure（UI 提示）。
- override 强制 mode 直接返不调 LLM（业务员 ⇧⌘↵ = 强制 new_cr）。
- LLM 错 / parse 错 → 兜底 new_cr + confidence=0.5 + is_unsure=True
  （保守 = 别误吃业务员的新需求）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

IntentMode = Literal["new_cr", "refine_cr", "chat_only"]


@dataclass(frozen=True)
class IntentDecision:
    """分类结果。"""
    mode: IntentMode
    confidence: float
    reason: str

    @property
    def is_unsure(self) -> bool:
        """confidence < 0.6 → UI 应该在输入框旁提示「我准备走 X，不对点这里」。"""
        return self.confidence < 0.6


class _ClassifierLLM(Protocol):
    """最小 LLM 接口（同 CompactionLLM 风格）。"""
    async def complete(self, prompt: str, *, model: str | None = None) -> str: ...


CLASSIFY_PROMPT = """\
你是 doskill 意图分类器。业务员在 web 改造场景里说了一句新消息，你判断这条消息走哪条路径。

三种 mode：
- **new_cr**：业务员要新做一个改动（新需求 / 新功能 / 全新的页面调整）
- **refine_cr**：业务员是在调整 / 修补上一次刚做完的修改（用「字号大一点」「颜色再淡点」「换成蓝色」「再加个图标」这类追加性描述）
- **chat_only**：业务员只是在讨论 / 评价 / 问问题 / 闲聊，不需要 AI 真去改代码（用「这个改得怎么样」「为啥用这种方案」「你能加搜索吗」这类疑问 / 评论性描述）

判断原则：
- 看消息的语气：追加修饰词 → refine；新动作动词 → new_cr；疑问 / 评价 → chat_only
- 看上下文：如果上一个 CR 是 preview-ready / merged 且业务员的话像在调整刚才的成果 → refine_cr
- 如果上一个 CR 是 failed → 业务员的话可能是讨论怎么修，倾向 chat_only
- 如果没有上一个 CR → 多半 new_cr 或 chat_only（按消息内容判）

返回严格 JSON（不要任何额外文字）：
{{"mode": "new_cr|refine_cr|chat_only", "confidence": 0.0-1.0, "reason": "简要说明"}}

confidence 给真实信心，别都给 0.99；不确定就给 0.4-0.6。

==== 项目知识（业务员的代码大概是这样的）====
{repo_doc}

==== 最近的对话历史（最近 {history_count} 条）====
{history}

==== 上一个 CR 的状态 ====
{last_cr_state}

==== 业务员刚说的新消息 ====
{new_message}
"""


class IntentClassifier:
    def __init__(self, llm: _ClassifierLLM):
        self._llm = llm

    async def classify(
        self,
        *,
        message_text: str,
        conversation_messages: list[dict[str, Any]],
        last_cr_state: str | None,
        repo_doc: str,
        override: IntentMode | None = None,
        history_n: int = 6,
    ) -> IntentDecision:
        # override：业务员显式指定，跳过 LLM
        if override is not None:
            return IntentDecision(
                mode=override,
                confidence=1.0,
                reason=f"业务员显式指定 {override}",
            )

        # 拼 prompt
        recent = conversation_messages[-history_n:] if conversation_messages else []
        history_text = "\n".join(
            f"[{m.get('type', '?')}] {m.get('content', '')}" for m in recent
        ) or "（无）"
        prompt = CLASSIFY_PROMPT.format(
            repo_doc=(repo_doc[:2000] if repo_doc else "（无）"),
            history_count=len(recent),
            history=history_text,
            last_cr_state=last_cr_state or "（无上一 CR）",
            new_message=message_text,
        )

        try:
            text = await self._llm.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent_classifier LLM 出错，兜底 new_cr：%s", exc)
            return _fallback()

        parsed = _try_parse(text)
        if parsed is None:
            logger.warning("intent_classifier LLM 返非 JSON，兜底 new_cr：%r", text[:200])
            return _fallback()
        return parsed


def _fallback() -> IntentDecision:
    """兜底：new_cr + 0.5 confidence + is_unsure=True。"""
    return IntentDecision(
        mode="new_cr",
        confidence=0.5,
        reason="LLM 出错或返回不可解析，兜底走 new_cr",
    )


_JSON_BLOCK = re.compile(r"\{[\s\S]*?\}")


def _try_parse(text: str) -> IntentDecision | None:
    """从 LLM 返回里提取 JSON object，校验字段。"""
    candidates: list[str] = [text.strip()]
    m = _JSON_BLOCK.search(text)
    if m:
        candidates.append(m.group(0))

    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        mode = data.get("mode")
        if mode not in ("new_cr", "refine_cr", "chat_only"):
            continue
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        reason = str(data.get("reason") or "")
        return IntentDecision(mode=mode, confidence=confidence, reason=reason)

    return None
