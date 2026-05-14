import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "mysql+pymysql://root:demopass@localhost:3306/demo_test",
)


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    from demo_backend.db import Base
    import demo_backend.models  # noqa: F401  保证模型被注册

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(test_engine):
    session = sessionmaker(bind=test_engine)()
    from demo_backend.models import AppSetting, Order, OrderItem

    session.query(OrderItem).delete()
    session.query(Order).delete()
    session.query(AppSetting).delete()
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(test_engine, db_session):
    from fastapi.testclient import TestClient

    from demo_backend.db import get_db
    from demo_backend.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
