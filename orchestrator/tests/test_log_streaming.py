"""Phase F：流式 log SSE 测试。

覆盖：
- EventBus.publish_log 发出 log 事件、含 phase/line/ts
- EventBus buffer 上限：超出 BUFFER_MAX_EVENTS 后 FIFO 丢老的
- _dev_runner_common.stream_subprocess 把每行子进程输出灌到 log sink
- Pipeline 在每个 phase 都至少发了 1 条 log marker
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from orchestrator.adapters.fakes import (
    FakeDevRunner,
    FakeInteractionSkill,
    FakePreviewAdapter,
    FakeStackAdapter,
)
from orchestrator.adapters.impl._dev_runner_common import stream_subprocess
from orchestrator.adapters.types import RawRequest
from orchestrator.events import BUFFER_MAX_EVENTS, Event, EventBus
from orchestrator.git_manager import GitManager
from orchestrator.pipeline import Pipeline
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository


# ── EventBus.publish_log ─────────────────────────────────────────────


async def test_publish_log_emits_log_event_with_phase_line_ts():
    bus = EventBus()
    await bus.publish_log("r1", phase="coding", line="正在分析 OrderList.tsx...")
    history = bus.history("r1")
    assert len(history) == 1
    evt = history[0]
    assert evt.type == "log"
    assert evt.data["phase"] == "coding"
    assert evt.data["line"] == "正在分析 OrderList.tsx..."
    assert evt.data["ts"]  # isoformat string


async def test_publish_log_skips_empty_or_whitespace_lines():
    bus = EventBus()
    await bus.publish_log("r1", "coding", "")
    await bus.publish_log("r1", "coding", "   \n")
    await bus.publish_log("r1", "coding", "real content")
    assert len(bus.history("r1")) == 1
    assert bus.history("r1")[0].data["line"] == "real content"


async def test_publish_log_truncates_long_lines_to_200_chars():
    bus = EventBus()
    long = "x" * 500
    await bus.publish_log("r1", "coding", long)
    line = bus.history("r1")[0].data["line"]
    assert len(line) == 200


async def test_buffer_caps_at_max_and_drops_old_fifo():
    bus = EventBus()
    # 发 BUFFER_MAX_EVENTS + 50 条
    overflow = 50
    for i in range(BUFFER_MAX_EVENTS + overflow):
        await bus.publish("r1", Event(type="log", data={"n": i}))
    hist = bus.history("r1")
    assert len(hist) == BUFFER_MAX_EVENTS
    # 最早的 50 条被丢，第一个剩下的应该是 n=50
    assert hist[0].data["n"] == overflow
    assert hist[-1].data["n"] == BUFFER_MAX_EVENTS + overflow - 1


async def test_subscribe_after_buffer_overflow_still_in_sync_with_live():
    """buffer 被 deque(maxlen) 截过头时，subscribe 不应在 queue 上重复回放历史。"""
    bus = EventBus()
    # 灌满 + 溢出
    for i in range(BUFFER_MAX_EVENTS + 10):
        await bus.publish("r2", Event(type="log", data={"n": i}))
    sub = bus.subscribe("r2")
    # 先回放 buffer 里现存的（共 BUFFER_MAX_EVENTS 个，已丢前 10 个）
    seen: list[int] = []
    for _ in range(BUFFER_MAX_EVENTS):
        evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
        seen.append(evt.data["n"])
    assert seen[0] == 10  # 前 10 个被 deque 丢了
    assert seen[-1] == BUFFER_MAX_EVENTS + 10 - 1
    # 再发一条新的，应只看到这一条（旧 queue 项已被跳过）
    await bus.publish("r2", Event(type="log", data={"n": 9999}))
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.data["n"] == 9999


# ── stream_subprocess ────────────────────────────────────────────────


async def test_stream_subprocess_publishes_each_line_via_log_sink():
    """起一个 echo 子进程；验 _stream_subprocess 把每行 publish 成 log event。"""
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", "echo line1; echo line2; echo line3",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    captured: list[str] = []

    async def _log(line: str) -> None:
        captured.append(line)

    stdout_text, stderr_text = await stream_subprocess(proc, _log, timeout=5)
    assert captured == ["line1", "line2", "line3"]
    assert "line1" in stdout_text
    assert stderr_text == ""
    assert proc.returncode == 0


async def test_stream_subprocess_log_exception_does_not_crash_drain():
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", "echo a; echo b",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _bad_log(line: str) -> None:
        raise RuntimeError("downstream blew up")

    stdout_text, _ = await stream_subprocess(proc, _bad_log, timeout=5)
    # 子进程正常完成，stdout 仍累计
    assert "a" in stdout_text and "b" in stdout_text
    assert proc.returncode == 0


async def test_stream_subprocess_timeout_raises():
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", "sleep 10",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _log(line: str) -> None:
        pass

    with pytest.raises(asyncio.TimeoutError):
        await stream_subprocess(proc, _log, timeout=0.3)
    proc.kill()
    await proc.wait()


# ── Pipeline 集成 ────────────────────────────────────────────────────


@pytest.fixture
def temp_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "f.txt").write_text("v1\n")
    run("add", "f.txt")
    run("commit", "-m", "init")
    return repo


def _raw() -> RawRequest:
    return RawRequest(
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={},
        viewport={},
        request_text="改个颜色",
    )


async def test_pipeline_emits_log_marker_in_each_phase(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    bus = EventBus()
    pipeline = Pipeline(
        repo_path=str(temp_repo),
        repository=repo,
        git_manager=GitManager(str(temp_repo)),
        event_bus=bus,
        quota=QuotaManager(capacity=5),
        interaction_skill=FakeInteractionSkill(question_count=0),
        stack_adapter=FakeStackAdapter(),
        dev_runner=FakeDevRunner(),
        preview_adapter=FakePreviewAdapter(),
    )
    await pipeline.run(cr.id)
    log_events = [e for e in bus.history(cr.id) if e.type == "log"]
    phases = {e.data["phase"] for e in log_events}
    # happy path 应至少覆盖这几个阶段
    assert "clarifying" in phases
    assert "locating" in phases
    assert "coding" in phases
    assert "building" in phases
    # 每个阶段至少 1 条
    for phase in ("clarifying", "locating", "coding", "building"):
        assert any(e.data["phase"] == phase for e in log_events), (
            f"phase {phase} 没发任何 log"
        )
