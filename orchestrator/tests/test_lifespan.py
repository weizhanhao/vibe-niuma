from orchestrator.adapters.types import RawRequest
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


def test_restart_recovery_marks_non_terminal_as_interrupted(
    test_engine, db_session, orchestrator_repo, monkeypatch
):
    """lifespan 启动时应把残留的非终态请求标 failed(interrupted)。"""
    # 先在 DB 里塞一条「卡在 coding」的请求（模拟上次进程崩溃残留）
    repo = ChangeRequestRepository(db_session)
    raw = RawRequest(
        url="http://x/orders", screenshot_b64="i", box_coords={},
        viewport={}, request_text="x",
    )
    cr = repo.create(raw)
    for s in (State.CLARIFYING, State.LOCATED, State.CODING):
        repo.transition(cr.id, s)
    assert repo.get(cr.id).state == State.CODING.value

    # 起 TestClient（触发 lifespan）
    from fastapi.testclient import TestClient

    from orchestrator import main as main_mod
    from orchestrator.db import get_db
    from orchestrator.events import EventBus
    from orchestrator.main import app, app_state
    from orchestrator.quota import QuotaManager

    monkeypatch.setattr(main_mod.settings, "demo_repo_path", str(orchestrator_repo))
    app_state.event_bus = EventBus()
    app_state.quota = QuotaManager(capacity=5)
    app_state.pipeline = None
    app_state.session_factory = lambda: db_session

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app):
            pass  # 进入再退出，lifespan 完整跑一遍（recovery + reaper 启动/取消）
    finally:
        app.dependency_overrides.clear()

    refreshed = repo.get(cr.id)
    assert refreshed.state == State.FAILED.value
    assert refreshed.fail_phase == "interrupted"
    assert refreshed.fail_reason == "orchestrator-restart"
