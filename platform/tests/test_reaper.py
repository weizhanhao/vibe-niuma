"""回收器测试 —— 这三件事之前只在注释里存在。"""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from vplatform.core import db as dbmod
from vplatform.core.models import (
    Job, JobArchive, PortLease, Requirement, Run, Step, Task, Workspace,
    next_requirement_seq,
)
from vplatform.orchestration.reaper import (
    archive_jobs, reap_port_leases, reap_workspaces,
)


def test_terminal_jobs_move_to_archive_with_their_steps(session, project):
    """§7.5 补偿 ②：MySQL 没有部分索引，热表只留活跃行。"""
    old = datetime.utcnow() - timedelta(hours=2)
    done = Job(project_id=project.id, kind="k", state="done",
               idempotency_key="a", updated_at=old)
    live = Job(project_id=project.id, kind="k", state="pending",
               idempotency_key="b", updated_at=old)
    session.add_all([done, live]); session.flush()
    session.add(Step(project_id=project.id, job_id=done.id, name="s1", state="done"))
    session.commit()

    assert archive_jobs(older_than_s=60) == 1

    with dbmod.session_scope() as s:
        assert s.query(Job).count() == 1                  # 活跃的留下
        assert s.query(Job).one().idempotency_key == "b"
        arch = s.query(JobArchive).one()
        assert arch.idempotency_key == "a" and arch.state == "done"
        assert s.query(Step).count() == 0                 # step 跟着走


def test_recent_terminal_jobs_are_not_archived_yet(session, project):
    """刚结束的先留着 —— 排查问题时还要看。"""
    session.add(Job(project_id=project.id, kind="k", state="done",
                    idempotency_key="a", updated_at=datetime.utcnow()))
    session.commit()
    assert archive_jobs(older_than_s=3600) == 0


def test_expired_port_leases_are_reclaimed(session, project):
    """端口是稀缺资源，租约过期必须回收，否则配额被僵尸占死。"""
    session.add_all([
        PortLease(project_id=project.id, port=5100, workspace_id="dead",
                  expires_at=datetime.utcnow() - timedelta(hours=1)),
        PortLease(project_id=project.id, port=5101, workspace_id="alive",
                  expires_at=datetime.utcnow() + timedelta(hours=1)),
    ])
    session.commit()
    assert reap_port_leases() == 1
    with dbmod.session_scope() as s:
        assert [p.port for p in s.query(PortLease).all()] == [5101]


def _run_row(session, project, key="T1"):
    """建一条真的 Run。

    sqlite 默认不检查外键，MySQL 会 —— 之前这里直接塞 run_id="r1"，
    sqlite 上过、MySQL 上炸。测试也要按真实约束写。
    """
    r = Requirement(project_id=project.id,
                    seq=next_requirement_seq(session, project.id),
                    title="t", requested_by="u")
    session.add(r); session.flush()
    t = Task(project_id=project.id, requirement_id=r.id, key=key, title="x")
    session.add(t); session.flush()
    run = Run(project_id=project.id, task_id=t.id, branch="b", state="done")
    session.add(run); session.flush()
    return run


def test_idle_workspaces_are_reclaimed(session, project, tmp_path):
    """**worker 崩溃后工位不能永久泄漏。**

    之前 Workspace 表零写入 —— 连"要收什么"都不知道，
    worktree + 容器 + 磁盘就永远留在那了。
    """
    ws_dir = tmp_path / "zombie"
    (ws_dir / "repo").mkdir(parents=True)
    run = _run_row(session, project)
    session.add(Workspace(project_id=project.id, run_id=run.id, path=str(ws_dir),
                          state="ready", repos={"repo": str(ws_dir / "repo")},
                          created_at=datetime.utcnow() - timedelta(hours=3)))
    session.commit()

    released = []

    class FakeProvider:
        async def release(self, handle, best_effort=False):
            released.append(str(handle.root))

    n = asyncio.run(reap_workspaces(FakeProvider(), idle_after_s=60))
    assert n == 1 and released == [str(ws_dir)]
    with dbmod.session_scope() as s:
        assert s.query(Workspace).one().state == "released"


def test_fresh_workspace_is_left_alone(session, project, tmp_path):
    """在跑的工位不能被收掉 —— 那会把 agent 的活儿腰斩。"""
    ws_dir = tmp_path / "busy"
    ws_dir.mkdir()
    run = _run_row(session, project)
    session.add(Workspace(project_id=project.id, run_id=run.id, path=str(ws_dir),
                          state="ready", repos={}, created_at=datetime.utcnow()))
    session.commit()

    class FakeProvider:
        async def release(self, handle, best_effort=False):
            raise AssertionError("不该收在跑的工位")

    assert asyncio.run(reap_workspaces(FakeProvider(), idle_after_s=3600)) == 0


def test_one_bad_workspace_does_not_stop_the_batch(session, project, tmp_path):
    """一个收不掉不能拖死整批。"""
    for i in (1, 2):
        d = tmp_path / f"w{i}"
        d.mkdir()
        run = _run_row(session, project, key=f"T{i}")
        session.add(Workspace(project_id=project.id, run_id=run.id, path=str(d),
                              state="ready", repos={},
                              created_at=datetime.utcnow() - timedelta(hours=3)))
    session.commit()

    class FlakyProvider:
        def __init__(self): self.n = 0
        async def release(self, handle, best_effort=False):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("docker daemon 没响应")

    assert asyncio.run(reap_workspaces(FlakyProvider(), idle_after_s=60)) == 1


def test_archiving_a_job_that_received_signals(session, project):
    """**收过信号的 job 也要能归档。**

    signals.job_id 有外键指向 jobs.id —— 不先删信号就是
    `Cannot delete or update a parent row`，整批归档回滚。
    而收过信号的 job 恰恰是最常见的那种（人工闸门、澄清挂起、重试唤醒
    都会留信号），于是归档永远失败、reaper 崩溃重试，热表只增不减 ——
    MySQL 没有部分索引，热表撑大正是归档要避免的那件事。
    """
    from datetime import datetime, timedelta

    from vplatform.core.models import Job, JobArchive, Signal
    from vplatform.orchestration.jobs import JobStore
    from vplatform.orchestration.reaper import archive_jobs

    store = JobStore(session)
    job = store.enqueue(project_id=project.id, kind="advance_requirement",
                        idempotency_key="k-signal", payload={})
    store.park(job)
    store.signal(job.id, "review_decision", {"decision": "approve"})
    job.state = "done"
    job.updated_at = datetime.utcnow() - timedelta(hours=2)
    session.commit()

    job_id = job.id
    assert archive_jobs(older_than_s=60) == 1
    session.expunge_all()      # 身份映射里那个已删对象会抛 ObjectDeletedError
    assert session.query(Job).filter_by(id=job_id).count() == 0
    assert session.query(JobArchive).filter_by(id=job_id).count() == 1
    assert session.query(Signal).filter_by(job_id=job_id).count() == 0
