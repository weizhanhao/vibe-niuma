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
