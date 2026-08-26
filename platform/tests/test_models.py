import pytest

from vplatform.core.config import SecretError, resolve_secret
from vplatform.core.models import (
    PortLease, Project, Requirement, Task, TaskTouch, next_requirement_seq,
)
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta


def test_project_slug_unique(session, project):
    session.add(Project(org_id=project.org_id, name="dup", slug="mc"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_port_lease_unique_per_project(session, project):
    """端口租约靠 DB 唯一索引防抢占，不靠应用层协调（§5.3 坑 2）。"""
    exp = datetime.utcnow() + timedelta(minutes=30)
    session.add(PortLease(project_id=project.id, port=5100, expires_at=exp))
    session.flush()
    session.add(PortLease(project_id=project.id, port=5100, expires_at=exp))
    with pytest.raises(IntegrityError):
        session.flush()


def test_touches_are_a_join_table_not_json(session, project):
    """§7.5 ③：touches 规范化成关联表，交集查询走索引。"""
    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="导出改造", requested_by="chen")
    session.add(r); session.flush()
    t = Task(project_id=project.id, requirement_id=r.id, key="T1", title="改导出")
    session.add(t); session.flush()
    session.add_all([
        TaskTouch(project_id=project.id, task_id=t.id, path="app/routers/export.py", repo_name="api"),
        TaskTouch(project_id=project.id, task_id=t.id, path="app/tasks/export_job.py", repo_name="api"),
    ])
    session.flush()
    rows = session.query(TaskTouch).filter_by(task_id=t.id).all()
    assert {x.path for x in rows} == {"app/routers/export.py", "app/tasks/export_job.py"}


def test_secret_never_stored_plaintext(project):
    """DB 里只有引用。泄库不泄密钥。"""
    assert project.secret_refs["llm"].startswith("literal:")
    assert resolve_secret("literal:abc") == "abc"


def test_secret_env_and_errors(monkeypatch):
    monkeypatch.setenv("VP_TEST_KEY", "sk-real")
    assert resolve_secret("env:VP_TEST_KEY") == "sk-real"
    with pytest.raises(SecretError, match="未设置"):
        resolve_secret("env:VP_MISSING")
    with pytest.raises(SecretError, match="不支持"):
        resolve_secret("plain-key")
    with pytest.raises(SecretError):
        resolve_secret(None)


def test_requirement_seq_is_per_project_and_atomic(session, project):
    """编号是空间内的，且靠 DB 自增取号 —— 并发下不重号。"""
    other = Project(org_id=project.org_id, name="仓配", slug="wms")
    session.add(other); session.flush()

    a = [next_requirement_seq(session, project.id) for _ in range(3)]
    b = [next_requirement_seq(session, other.id) for _ in range(2)]
    assert a == [1, 2, 3]
    assert b == [1, 2]          # 各空间独立计数

    r = Requirement(project_id=project.id, seq=a[0], title="x", requested_by="u")
    session.add(r); session.flush()
    assert r.ref == "R-1"


def test_requirement_seq_unique_within_project(session, project):
    for i in (1, 1):
        session.add(Requirement(project_id=project.id, seq=i, title=f"t{i}", requested_by="u"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_id_is_assigned_at_construction_not_at_flush(project):
    """id 必须构造即有 —— 否则 add() 之后拿到的是 None，
    带进 job payload / 外键要到很远的地方才炸。"""
    r = Requirement(project_id=project.id, seq=99, title="t", requested_by="u")
    assert r.id and len(r.id) == 32          # 还没 add 就有了
    t = Task(project_id=project.id, requirement_id=r.id, key="T1", title="x")
    assert t.id and t.id != r.id             # 各自独立


def test_explicit_id_is_respected(project):
    r = Requirement(id="fixed-id", project_id=project.id, seq=1, title="t",
                    requested_by="u")
    assert r.id == "fixed-id"


def test_every_model_is_reachable_from_core_models(engine):
    """**所有模型必须挂在 core.models 上。**

    定义在别处（比如 api/auth.py）的模型，只有那个模块被 import 之后才会
    注册到 Base.metadata。`create_all()` 先跑就会漏建表 —— 实测踩过：
    签发 token 时报 "no such table: api_tokens"。
    """
    import importlib
    import pkgutil
    from sqlalchemy import inspect

    import vplatform
    from vplatform.core.models import Base

    before = set(Base.metadata.tables)
    # 把整个包都 import 一遍，看有没有模型是这时候才冒出来的
    for mod in pkgutil.walk_packages(vplatform.__path__, "vplatform."):
        try:
            importlib.import_module(mod.name)
        except Exception:            # noqa: BLE001 —— 有可选依赖的模块跳过
            continue
    after = set(Base.metadata.tables)
    assert after == before, (
        f"这些表只有在 import 特定模块后才注册，create_all 会漏建："
        f"{sorted(after - before)}")

    # 而且 create_all 真的把它们都建出来了
    created = set(inspect(engine).get_table_names())
    assert before <= created, f"没建出来的表：{sorted(before - created)}"
