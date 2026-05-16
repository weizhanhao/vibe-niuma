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
    cr_id = cr.id  # 在 lifespan close session 前缓存 id（之后 cr 会 detached）
    for s in (State.CLARIFYING, State.LOCATED, State.CODING):
        repo.transition(cr_id, s)
    assert repo.get(cr_id).state == State.CODING.value

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
    app_state.pipelines = {}
    app_state.session_factory = lambda: db_session

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app):
            pass  # 进入再退出，lifespan 完整跑一遍（recovery + reaper 启动/取消）
    finally:
        app.dependency_overrides.clear()

    # lifespan recovery 在内部用 session_factory（被 monkeypatch 成 lambda: db_session）
    # 起新 session 改 row、提交后 close —— 我们这条 cr 实例随之 detached。
    # 用一条原生 SQL 绕开 ORM identity map，拿到最权威的 DB 状态。
    from sqlalchemy import text
    with test_engine.connect() as conn:
        row = conn.execute(
            text("SELECT state, fail_phase, fail_reason FROM change_requests WHERE id = :id"),
            {"id": cr_id},
        ).fetchone()
    assert row is not None
    assert row[0] == State.FAILED.value
    assert row[1] == "interrupted"
    assert row[2] == "orchestrator-restart"
