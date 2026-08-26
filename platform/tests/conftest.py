import pytest
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch):
    """默认开发模式，让既有测试的 X-User 生效。
    专门测认证边界的用例自己 delenv 关掉它。"""
    monkeypatch.setenv("VP_DEV_AUTH", "1")

from vplatform.core import db as dbmod
from vplatform.core.models import Base, Org, Project


@pytest.fixture()
def engine(tmp_path):
    """默认 sqlite（快）。设 VP_TEST_MYSQL_URL 就整套改跑 MySQL。

    MySQL 上跑一遍是必须的：`SKIP LOCKED`、`LONGTEXT` variant、
    utf8mb4 索引长度这些实现在 sqlite 上根本不会被执行
    （jobs.py 对 sqlite 显式关掉了 SKIP LOCKED）。
    """
    import os
    mysql_url = os.environ.get("VP_TEST_MYSQL_URL")
    if mysql_url:
        eng = dbmod.init_engine(mysql_url)
        Base.metadata.drop_all(eng)
        Base.metadata.create_all(eng)
    else:
        eng = dbmod.init_engine(f"sqlite:///{tmp_path}/t.db", create_all=True)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine) -> Session:
    """裸 session —— 不用 session_scope，因为测约束违反时会留下待回滚状态，
    fixture 退出再 commit 就会二次爆炸，掩盖真正的断言。"""
    s = dbmod._Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def project(session) -> Project:
    """**提交**而不是 flush —— 事件总线/worker 会开自己的 session，
    未提交的数据它们看不见，sqlite 上还会直接锁死。"""
    org = Org(name="acme")
    session.add(org)
    session.flush()
    p = Project(org_id=org.id, name="商户中台", slug="mc",
                secret_refs={"llm": "literal:test-key"})
    session.add(p)
    session.commit()
    return p
