"""数据库会话。MySQL 8（D4）；测试走 sqlite。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from vplatform.core.config import get_settings
from vplatform.core.models import Base

_engine: Engine | None = None
_Session: sessionmaker | None = None


def init_engine(url: str | None = None, *, create_all: bool = False) -> Engine:
    global _engine, _Session
    url = url or get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs.pop("pool_pre_ping")
    _engine = create_engine(url, **kwargs)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    if create_all:
        Base.metadata.create_all(_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return init_engine()
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务边界。异常回滚，正常提交。"""
    if _Session is None:
        init_engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
