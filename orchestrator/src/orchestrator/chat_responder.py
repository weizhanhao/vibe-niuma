"""Plan 10 Task 4: chat_responder (chat_only 路径)。

业务员说「你觉得这次改的怎么样？」「为啥用这种方案？」走这条 ——
AI 纯文字回复，不进 pipeline / 不写代码 / 不切 branch / 不起 docker。

设计要点（spec §Architecture）：
- intent_classifier 判 chat_only → 调 ChatResponder.respond()
- LLM prompt 严约束「只回答业务问题，不写代码、不承诺改东西」
  避免 AI 答「好的我去改」让业务员误以为真启动了 CR
- AI 回复 append 一条 type=ai message 到 conversation；下次 LLM 调度可见
- 失败 graceful：LLM 错就 raise（caller 决定怎么显示给业务员）
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol

from orchestrator.conversation import ConversationRepository

logger = logging.getLogger(__name__)


CHAT_PROMPT = """\
你是 doskill 助手，业务员在跟你聊 web 改造场景里的话题。

你的角色：
- 你**只回答业务问题**或给建议。不要写代码、不要承诺改东西、不要说「好的我去改」
- 如果业务员真的想让你改，告诉他「想让我改的话点输入框旁边的『+ 框选』或直接说出新需求」
- 答案要简短，1-3 句话，业务员看得懂

==== 项目知识（业务员的代码大概是这样的）====
{repo_doc}

==== 最近的对话历史 ====
{history}

==== 业务员刚说的新消息 ====
{user_message}

请直接给回答（不要 JSON、不要 markdown、不要前缀）：
"""


class _LLM(Protocol):
    async def complete(self, prompt: str, *, model: str | None = None) -> str: ...


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


class ChatResponder:
    def __init__(self, llm: _LLM, conversation_repo: ConversationRepository):
        self._llm = llm
        self._repo = conversation_repo

    async def respond(
        self,
        *,
        conversation_id: str,
        user_message: str,
        repo_doc: str,
        history_n: int = 6,
    ) -> str:
        """跑 chat_only 一轮：拉历史 → 调 LLM → append AI 回复 → 返回 reply text。

        Raises:
            KeyError: conversation 不存在
        """
        conv = self._repo.get(conversation_id)
        if conv is None:
            raise KeyError(f"Conversation not found: {conversation_id}")

        recent = list(conv.messages or [])[-history_n:]
        history_text = "\n".join(
            f"[{m.get('type', '?')}] {m.get('content', '')}" for m in recent
        ) or "（无）"
        prompt = CHAT_PROMPT.format(
            repo_doc=(repo_doc[:2000] if repo_doc else "（无）"),
            history=history_text,
            user_message=user_message,
        )

        reply = await self._llm.complete(prompt)
        reply = (reply or "").strip()

        ai_msg: dict[str, Any] = {
            "type": "ai",
            "ts": _now_iso(),
            "content": reply,
        }
        self._repo.append_message(conversation_id, ai_msg)
        return reply
