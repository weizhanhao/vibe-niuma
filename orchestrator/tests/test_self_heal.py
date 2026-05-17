"""Plan 10 Task 5b: self_heal 失败自愈测试。

业务员视角（用户原话）：「直接用 AI 解决问题，搞不定再叫业务员」
  - CR 失败 → AI 看 fail_log + 项目知识 → 决定 retry / 改 prompt / 升级业务员
  - 最多自愈 2 次（防死循环）
  - 仍败 → chat 流 inline 一条 ai message：「我试了 X、Y 都不行，
    需要你帮：1) ... 2) ...」业务员补充信息后下条 message 走 new_cr
"""
from __future__ import annotations

import pytest

from orchestrator.self_heal import SelfHealClassifier, SelfHealDecision


class _FakeLLM:
    def __init__(self, response: str | None = None, raises: bool = False):
        self.response = response or '{"action":"retry","strategy":"端口冲突，重试"}'
        self.raises = raises
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, model: str | None = None) -> str:
        self.prompts.append(prompt)
        if self.raises:
            raise RuntimeError("LLM 挂了")
        return self.response


# ── 基本 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_action_strategy():
    llm = _FakeLLM('{"action":"retry","strategy":"docker port race"}')
    clf = SelfHealClassifier(llm=llm)
    d = await clf.classify(
        fail_log="docker compose up 报 port already in use",
        fail_phase="building",
        fail_reason="container",
        repo_doc="",
        chat_history=[],
    )
    assert isinstance(d, SelfHealDecision)
    assert d.action == "retry"
    assert "port" in d.strategy.lower()


@pytest.mark.asyncio
async def test_classify_routes_port_conflict_to_retry():
    llm = _FakeLLM('{"action":"retry","strategy":"端口竞争，等一下重试"}')
    clf = SelfHealClassifier(llm=llm)
    d = await clf.classify(
        fail_log="port 5101 already in use",
        fail_phase="building",
        fail_reason="container",
        repo_doc="",
        chat_history=[],
    )
    assert d.action == "retry"


@pytest.mark.asyncio
async def test_classify_routes_prompt_issue_to_retry_with_revised_prompt():
    llm = _FakeLLM(
        '{"action":"retry_with_revised_prompt",'
        '"strategy":"prompt 缺组件名，加上具体路径再试"}'
    )
    clf = SelfHealClassifier(llm=llm)
    d = await clf.classify(
        fail_log="dev_runner 没找到组件 OrderBadge",
        fail_phase="coding",
        fail_reason="no-changes",
        repo_doc="",
        chat_history=[],
    )
    assert d.action == "retry_with_revised_prompt"


@pytest.mark.asyncio
async def test_classify_routes_missing_api_key_to_escalate():
    llm = _FakeLLM(
        '{"action":"escalate","escalation_advice":'
        '["DEEPSEEK_API_KEY 没配","在设置里填一下"]}'
    )
    clf = SelfHealClassifier(llm=llm)
    d = await clf.classify(
        fail_log="401 Unauthorized: missing API key",
        fail_phase="coding",
        fail_reason="runner-error",
        repo_doc="",
        chat_history=[],
    )
    assert d.action == "escalate"
    assert d.escalation_advice is not None
    assert len(d.escalation_advice) == 2


@pytest.mark.asyncio
async def test_classify_prompt_includes_fail_log():
    llm = _FakeLLM()
    clf = SelfHealClassifier(llm=llm)
    await clf.classify(
        fail_log="特定错误关键字 XYZ_UNIQUE",
        fail_phase="coding",
        fail_reason="runner-error",
        repo_doc="React 项目",
        chat_history=[],
    )
    assert "XYZ_UNIQUE" in llm.prompts[0]
    assert "React" in llm.prompts[0]


@pytest.mark.asyncio
async def test_classify_falls_back_to_escalate_on_llm_error():
    """LLM 挂了 → 兜底 escalate（保守：让业务员介入比无限自愈安全）。"""
    llm = _FakeLLM(raises=True)
    clf = SelfHealClassifier(llm=llm)
    d = await clf.classify(
        fail_log="x",
        fail_phase="coding",
        fail_reason="x",
        repo_doc="",
        chat_history=[],
    )
    assert d.action == "escalate"
    assert d.escalation_advice is not None
    assert any("AI" in a or "失败" in a for a in d.escalation_advice)


@pytest.mark.asyncio
async def test_classify_falls_back_on_unparseable_response():
    llm = _FakeLLM("我也不知道哈哈")
    clf = SelfHealClassifier(llm=llm)
    d = await clf.classify(
        fail_log="x", fail_phase="coding", fail_reason="x",
        repo_doc="", chat_history=[],
    )
    assert d.action == "escalate"


# ── 上限 ────────────────────────────────────────────────────────────


def test_max_self_heal_attempts_constant():
    """业务员能感知的上限 = 2（防死循环 + 防 cost 爆）。"""
    from orchestrator.self_heal import MAX_SELF_HEAL_ATTEMPTS
    assert MAX_SELF_HEAL_ATTEMPTS == 2


# ── escalation message 拼接 helper ─────────────────────────────────


def test_format_escalation_message_includes_what_tried_and_advice():
    """格式化给业务员看的 chat 消息：「我试了 X、Y 都不行，建议你...」"""
    from orchestrator.self_heal import format_escalation_message
    msg = format_escalation_message(
        attempts=2,
        last_fail_phase="building",
        last_fail_reason="container",
        advice=["docker port 冲突，重试也没用", "重启 docker 试试"],
    )
    assert "试了" in msg
    assert "2" in msg or "两" in msg
    assert "docker port 冲突，重试也没用" in msg
    assert "重启 docker 试试" in msg
