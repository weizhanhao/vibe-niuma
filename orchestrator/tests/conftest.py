import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "mysql+pymysql://root:demopass@localhost:3307/orchestrator_test",
)


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    from orchestrator.db import Base
    import orchestrator.models  # noqa: F401  保证模型被注册

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(test_engine):
    session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)()
    from orchestrator.models import ChangeRequest

    session.query(ChangeRequest).delete()
    session.commit()
    yield session
    session.close()


@pytest.fixture
def orchestrator_repo(tmp_path):
    """给 orchestrator 操作用的一次性 git 仓库（有 main + 一个初始提交）。"""
    import subprocess

    repo = tmp_path / "target-repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(
        ["git", *a], cwd=repo, check=True, capture_output=True
    )
    run("init", "-b", "main")
    run("config", "user.email", "test@test")
    run("config", "user.name", "test")
    (repo / "file.txt").write_text("v1\n")
    run("add", "file.txt")
    run("commit", "-m", "init")
    return repo


@pytest.fixture
def client(test_engine, db_session, orchestrator_repo, monkeypatch):
    from fastapi.testclient import TestClient

    from orchestrator import main as main_mod
    from orchestrator.adapters.fakes import (
        FakeDevRunner, FakeInteractionSkill, FakePreviewAdapter, FakeStackAdapter,
    )
    from orchestrator.db import get_db
    from orchestrator.events import EventBus
    from orchestrator.git_manager import GitManager
    from orchestrator.main import app, app_state
    from orchestrator.pipeline import Pipeline
    from orchestrator.quota import QuotaManager
    from orchestrator.repository import ChangeRequestRepository

    # orchestrator 操作一次性 temp 仓库，不碰真实项目仓库
    monkeypatch.setattr(main_mod.settings, "demo_repo_path", str(orchestrator_repo))

    # 每个测试用全新的事件总线 + 配额，避免跨测试串味
    app_state.event_bus = EventBus()
    app_state.quota = QuotaManager(capacity=5)
    app_state.pipelines = {}
    app_state.session_factory = lambda: db_session

    # Plan 3 后 build_pipeline 默认走真实 adapter；测试需要 fake 装配
    def _fake_pipeline(db):
        return Pipeline(
            repo_path=str(orchestrator_repo),
            repository=ChangeRequestRepository(db),
            git_manager=GitManager(str(orchestrator_repo)),
            event_bus=app_state.event_bus,
            quota=app_state.quota,
            interaction_skill=FakeInteractionSkill(question_count=0),
            stack_adapter=FakeStackAdapter(),
            dev_runner=FakeDevRunner(),
            preview_adapter=FakePreviewAdapter(),
        )
    app_state.pipeline_factory = _fake_pipeline

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    # 还原 pipeline_factory 默认（避免污染下个测试）
    app_state.pipeline_factory = app_state._build_real_pipeline
