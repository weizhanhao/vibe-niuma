"""AgentSession 接口（§6）—— 修 v1 的头号问题 P1。

v1 的 `opencode run "<prompt>"` 是一次性子进程，跑完即死；续改靠把历史
拼成文本塞进新 prompt，再让 agent 自己 `git diff` 把上轮改动"猜"回来。
代价：tool-call 轨迹、已读文件、推理链每轮全丢，token 重复烧，refine 质量低一档。

这里把会话当一等公民：
    create  新需求 → 新 session
    send    refine 续改 → 复用 session_id，上下文还在
    fork    拆并行子任务 → 从父 session 分叉，天然继承上下文
    stream  实时轨迹
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class AgentEvent:
    kind: str            # log | tool | message | done | error
    text: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class AgentReply:
    session_id: str
    text: str = ""
    events: list[AgentEvent] = field(default_factory=list)


class AgentError(RuntimeError):
    """本层失败统一以此暴露。"""


@runtime_checkable
class AgentSession(Protocol):
    async def create(self, *, cwd: str, title: str,
                     parent: str | None = None) -> str: ...

    async def send(self, session_id: str, prompt: str, *, cwd: str,
                   timeout: float | None = None, fork: bool = False,
                   title: str = "") -> AgentReply: ...

    async def fork(self, session_id: str, *, cwd: str) -> str: ...

    def stream(self, session_id: str) -> AsyncIterator[AgentEvent]: ...
