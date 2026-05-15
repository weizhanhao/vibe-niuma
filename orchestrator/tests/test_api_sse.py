"""SSE 端点测试。

`GET /change-requests/{id}/events` 的实现就是 `app_state.event_bus.subscribe(id)`：
先回放 buffer 历史、再实时推。直接对同一个 event_bus 的 history() 断言，
等价于断言「一个 SSE 客户端会收到什么」—— 且不会踩 TestClient 同步流式 +
无限 SSE 生成器的死锁。
"""
import time


def _payload():
    return {
        "url": "http://x/orders",
        "screenshot_b64": "img",
        "box_coords": {},
        "viewport": {},
        "request_text": "把保存按钮改成蓝色",
    }


def _wait_state(client, rid, target, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.get(f"/change-requests/{rid}").json()["state"] == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"{rid} 未到达 {target}")


def test_sse_replays_status_events_after_pipeline_done(client):
    from orchestrator.main import app_state

    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "preview-ready")
    # SSE 客户端订阅时会回放整段历史 —— 这里直接断言这段历史
    events = app_state.event_bus.history(rid)
    states = [e.data["state"] for e in events if e.type == "status"]
    assert "clarifying" in states
    assert "preview-ready" in states


def test_sse_emits_question_events_during_clarification(client, monkeypatch):
    # 把 app 装配的 InteractionSkill 换成会问 1 个问题的版本
    from orchestrator import main as main_mod
    from orchestrator.adapters.fakes import (
        FakeDevRunner,
        FakeInteractionSkill,
        FakePreviewAdapter,
        FakeStackAdapter,
    )
    from orchestrator.git_manager import GitManager
    from orchestrator.main import app_state
    from orchestrator.pipeline import Pipeline
    from orchestrator.repository import ChangeRequestRepository

    def build_with_question(self, db):
        return Pipeline(
            repo_path=main_mod.settings.demo_repo_path,
            repository=ChangeRequestRepository(db),
            git_manager=GitManager(main_mod.settings.demo_repo_path),
            event_bus=self.event_bus,
            quota=self.quota,
            interaction_skill=FakeInteractionSkill(question_count=1),
            stack_adapter=FakeStackAdapter(),
            dev_runner=FakeDevRunner(),
            preview_adapter=FakePreviewAdapter(),
        )

    monkeypatch.setattr(main_mod.AppState, "build_pipeline", build_with_question)

    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "clarifying")
    # SSE 历史里应有一个 question 事件
    questions = [e for e in app_state.event_bus.history(rid) if e.type == "question"]
    assert len(questions) >= 1
    qid = questions[0].data["question_id"]
    # 回答它，流水线应能继续推进到 preview-ready
    client.post(
        f"/change-requests/{rid}/answer",
        json={"question_id": qid, "answer": "更显眼"},
    )
    _wait_state(client, rid, "preview-ready")
    assert client.get(f"/change-requests/{rid}").json()["state"] == "preview-ready"
