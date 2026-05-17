"""Plan 10 Task 5b: 失败自愈分类器。

业务员原话：「直接用 AI 解决问题，搞不定再叫业务员」

CR 失败后 pipeline 调 SelfHealClassifier 判断怎么做：
- 'retry'：临时错（端口冲突 / 网络抖动），直接重试就行
- 'retry_with_revised_prompt'：prompt 不够清楚，AI 自己改 prompt 再 retry
- 'escalate'：环境问题（缺密钥 / 缺工具 / AI 看不懂的代码），直接告业务员

最多自愈 2 次（防死循环 + 防 cost 爆）。
仍败 → format_escalation_message 拼一段中文给业务员看的提示，
通过 chat_responder.append 写到 conversation。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)


# 上限：业务员能感知的次数 = 2（>2 体感「卡死」）
MAX_SELF_HEAL_ATTEMPTS = 2


SelfHealAction = Literal["retry", "retry_with_revised_prompt", "escalate"]


@dataclass(frozen=True)
class SelfHealDecision:
    """自愈分类结果。"""
    action: SelfHealAction
    strategy: str = ""              # retry_with_revised_prompt 用
    escalation_advice: list[str] | None = None  # escalate 用


class _LLM(Protocol):
    async def complete(self, prompt: str, *, model: str | None = None) -> str: ...


SELF_HEAL_PROMPT = """\
你是 doskill 自愈诊断器。业务员的一个 CR 跑挂了，你看 fail_log 决定怎么处理。

三种行动：
- **retry**：临时性错误（端口冲突 / 网络抖动 / docker race），原样重试就行
- **retry_with_revised_prompt**：prompt 不够清楚导致 AI 改错地方，给 strategy 字段提示「改 prompt 时加上 XX」让下一轮 dev_runner 看到
- **escalate**：环境问题（缺 API key / 缺工具 / 业务员要做手动配置），AI 自己解决不了，给 escalation_advice 数组（2-3 条业务员能读懂的建议）

返回严格 JSON（不要任何额外文字）：
- retry: {{"action": "retry", "strategy": "原因简述"}}
- retry_with_revised_prompt: {{"action": "retry_with_revised_prompt", "strategy": "下一轮 prompt 要加上 ..."}}
- escalate: {{"action": "escalate", "escalation_advice": ["建议 1", "建议 2", ...]}}

==== 项目知识 ====
{repo_doc}

==== 最近 chat history ====
{history}

==== 失败信息 ====
phase: {fail_phase}
reason: {fail_reason}
log:
{fail_log}
"""


class SelfHealClassifier:
    def __init__(self, llm: _LLM):
        self._llm = llm

    async def classify(
        self,
        *,
        fail_log: str,
        fail_phase: str,
        fail_reason: str,
        repo_doc: str,
        chat_history: list[dict[str, Any]],
        history_n: int = 4,
    ) -> SelfHealDecision:
        recent = chat_history[-history_n:] if chat_history else []
        history_text = "\n".join(
            f"[{m.get('type', '?')}] {m.get('content', '')}" for m in recent
        ) or "（无）"
        prompt = SELF_HEAL_PROMPT.format(
            repo_doc=(repo_doc[:1500] if repo_doc else "（无）"),
            history=history_text,
            fail_phase=fail_phase,
            fail_reason=fail_reason,
            fail_log=(fail_log[:2000] if fail_log else "（无 log）"),
        )

        try:
            text = await self._llm.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("self_heal LLM 出错，兜底 escalate：%s", exc)
            return _fallback_escalate()

        parsed = _try_parse(text)
        if parsed is None:
            logger.warning("self_heal LLM 返非 JSON，兜底 escalate：%r", text[:200])
            return _fallback_escalate()
        return parsed


def _fallback_escalate() -> SelfHealDecision:
    return SelfHealDecision(
        action="escalate",
        escalation_advice=[
            "AI 自愈失败：诊断器自己出错了，需要你看一下",
            "建议先看 ECS 日志（journalctl -u doskill-orchestrator）",
        ],
    )


_JSON_BLOCK = re.compile(r"\{[\s\S]*?\}")


def _try_parse(text: str) -> SelfHealDecision | None:
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
        action = data.get("action")
        if action not in ("retry", "retry_with_revised_prompt", "escalate"):
            continue
        strategy = str(data.get("strategy") or "")
        advice = data.get("escalation_advice")
        if advice is not None and not isinstance(advice, list):
            advice = None
        return SelfHealDecision(
            action=action,
            strategy=strategy,
            escalation_advice=advice,
        )
    return None


def format_escalation_message(
    *,
    attempts: int,
    last_fail_phase: str,
    last_fail_reason: str,
    advice: list[str],
) -> str:
    """拼一段中文给业务员看的 chat 消息（写进 conversation.messages.type=ai）。"""
    lines = [
        f"我试了 {attempts} 次都没成功（最后一次在 {last_fail_phase} 阶段 {last_fail_reason}）。",
        "需要你帮我看一下：",
    ]
    for i, a in enumerate(advice, 1):
        lines.append(f"  {i}) {a}")
    lines.append("")
    lines.append("处理好之后告诉我，我接着试。")
    return "\n".join(lines)
