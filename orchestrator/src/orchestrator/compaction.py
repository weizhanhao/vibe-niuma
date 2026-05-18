"""Plan 9 Task 4: conversation compaction —— 估 token + 老 AI 消息压成摘要。

设计要点（spec §Task 4）：
- estimate_tokens 用 tiktoken cl100k_base；中文 / 跨语言 ±10% 就行
- compact(messages, llm, threshold_soft=40_000, threshold_hard=56_000) → 新 messages
  - < threshold_soft → 原样返回（idempotent）
  - >= threshold_soft → 跑压缩
    - 必保留：所有 type=user 的消息；最近 6 个 user/ai 各 1 条（共 12 条）；
      标 `preserve=True` 的 ai 消息；老 summary 消息
    - 可压：剩下的 ai 消息原文 → 拼一段 plain text → 给 LLM 出 summary
    - 把 summary 当 type=summary 插在 head_keep 后、tail 前
    - 老 messages 不删（调用方拿到的是新 list，原 list 不动）
- LLM 通过依赖注入（CompactionLLM Protocol），主代码用 _DefaultCompactionLLM
  调 _llm.LLMClient.complete()；测试注入 fake 不联网
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


COMPACTION_PROMPT = """\
你是 vibe-niuma 对话压缩器。下面是业务员和 AI 的多轮对话。

要求：把所有 AI 回复（除了被标记 [PRESERVE] 的）压成一段中文摘要，包含：
1) 业务员的核心意图演化（按时间顺序串出来）
2) 已完成的修改（每个一句，引用 cr_xxx）
3) 未决问题 / 业务员的偏好

约束：
- ≤ 800 字
- 不省决策（"业务员选了方向 A"），只省过程（"AI 正在改..."）
- 输出纯文本，没有 markdown 装饰

对话：
{messages}
"""


# ── token 估算 ──────────────────────────────────────────────────────


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """用 tiktoken cl100k_base 估 token 数。空 list 返回 0。"""
    if not messages:
        return 0
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    blob = "\n".join(str(m.get("content", "")) for m in messages)
    return len(enc.encode(blob))


# ── LLM 接口 ────────────────────────────────────────────────────────


class CompactionLLM(Protocol):
    """压缩器调用的最小 LLM 接口：传 prompt 返回摘要文本。"""

    async def summarize(self, prompt: str) -> str: ...


@dataclass
class _DefaultCompactionLLM:
    """默认实现：用现有 _llm.LLMClient.complete()。"""

    model: str | None = None

    async def summarize(self, prompt: str) -> str:
        from orchestrator.adapters.impl._llm import LLMClient

        client = LLMClient()
        return await client.complete(prompt, model=self.model)


# ── compact ────────────────────────────────────────────────────────


async def compact(
    messages: list[dict[str, Any]],
    *,
    llm: CompactionLLM | None = None,
    threshold_soft: int = 40_000,
    threshold_hard: int = 56_000,
    keep_recent_pairs: int = 6,
) -> list[dict[str, Any]]:
    """压缩老 AI 消息为一段 summary，返回新 messages list（原 list 不动）。"""
    del threshold_hard  # 占位；当前只用 soft

    if estimate_tokens(messages) < threshold_soft:
        return list(messages)

    keep_recent_count = keep_recent_pairs * 2
    boundary = max(0, len(messages) - keep_recent_count)

    head = messages[:boundary]
    tail = messages[boundary:]

    compress_pool: list[dict[str, Any]] = []
    head_keep: list[dict[str, Any]] = []
    for m in head:
        if m.get("type") == "user":
            head_keep.append(m)
        elif m.get("type") == "ai" and m.get("preserve") is True:
            head_keep.append(m)
        elif m.get("type") == "summary":
            head_keep.append(m)
        else:
            compress_pool.append(m)

    if not compress_pool:
        return list(messages)

    rendered = "\n".join(
        f"[{m.get('type', '?')}@{m.get('ts', '?')}] {m.get('content', '')}"
        for m in compress_pool
    )
    prompt = COMPACTION_PROMPT.format(messages=rendered)
    used_llm = llm or _DefaultCompactionLLM()
    summary_text = await used_llm.summarize(prompt)

    summary_ts = compress_pool[0].get("ts", "")
    summary_msg = {
        "type": "summary",
        "ts": summary_ts,
        "content": summary_text,
        "replaces_count": len(compress_pool),
    }

    return [*head_keep, summary_msg, *tail]
