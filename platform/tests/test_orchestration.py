"""编排层测试。用 asyncio.run() 而不是 pytest-asyncio —— 少一个依赖。"""
import asyncio
from datetime import datetime, timedelta

import pytest

from vplatform.core import db as dbmod
from vplatform.core.models import Job, Step
from vplatform.orchestration.jobs import BACKGROUND, INTERACTIVE, JobStore
from vplatform.orchestration.worker import JobContext, Registry, Worker


def _store(session):
    return JobStore(session)


def test_enqueue_is_idempotent(session, project):
    st = _store(session)
    a = st.enqueue(project_id=project.id, kind="k", idempotency_key="same")
    b = st.enqueue(project_id=project.id, kind="k", idempotency_key="same")
    assert a.id == b.id
    assert session.query(Job).count() == 1


def test_claim_marks_running_and_increments_attempts(session, project):
    st = _store(session)
    st.enqueue(project_id=project.id, kind="k", idempotency_key="i1")
    job = st.claim(worker_id="w1")
    assert job is not None and job.state == "running"
    assert job.locked_by == "w1" and job.attempts == 1
    # 已被占，再抢抢不到
    assert st.claim(worker_id="w2") is None


def test_claim_respects_lane(session, project):
    st = _store(session)
    st.enqueue(project_id=project.id, kind="k", idempotency_key="bg", lane=BACKGROUND)
    assert st.claim(worker_id="w", lane=INTERACTIVE) is None
    assert st.claim(worker_id="w", lane=BACKGROUND) is not None


def test_claim_reclaims_stale_lock(session, project):
    """worker 崩溃后，超时的锁必须能被别人接管，否则 job 永远卡住。"""
    st = _store(session)
    st.enqueue(project_id=project.id, kind="k", idempotency_key="i1")
    job = st.claim(worker_id="dead")
    job.locked_at = datetime.utcnow() - timedelta(hours=2)
    session.flush()
    again = st.claim(worker_id="alive", lock_timeout_s=60)
    assert again is not None and again.locked_by == "alive"


def test_delay_hides_job_until_due(session, project):
    st = _store(session)
    st.enqueue(project_id=project.id, kind="k", idempotency_key="i1", delay_s=300)
    assert st.claim(worker_id="w") is None


def test_park_then_signal_wakes_and_promotes_lane(session, project):
    """人工 gate：挂起不占 worker；信号到达立刻拉回并升到交互 lane。"""
    st = _store(session)
    job = st.enqueue(project_id=project.id, kind="review", idempotency_key="i1")
    st.claim(worker_id="w")
    st.park(job)
    assert job.state == "awaiting_signal"
    assert st.claim(worker_id="w") is None            # 挂起期间不占 worker

    st.signal(job.id, "approved", {"by": "zhao"})
    assert job.state == "pending"
    assert job.lane == INTERACTIVE                     # 人在等 → 升 lane
    woken = st.claim(worker_id="w", lane=INTERACTIVE)
    assert woken is not None
    sig = st.take_signal(job.id, "approved")
    assert sig is not None and sig.payload["by"] == "zhao"
    assert st.take_signal(job.id, "approved") is None  # 只能消费一次


def test_retry_until_max_then_failed(session, project):
    st = _store(session)
    job = st.enqueue(project_id=project.id, kind="k", idempotency_key="i1", max_attempts=2)
    st.claim(worker_id="w")
    assert st.retry_later(job, delay_s=0, error="boom") is True
    st.claim(worker_id="w")
    assert st.retry_later(job, delay_s=0, error="boom") is False
    assert job.state == "failed" and "boom" in job.last_error


def test_step_is_idempotent_across_replay(session, project):
    """已完成的 step 重放时跳过 —— 进程被 kill 也不会重复副作用。"""
    st = _store(session)
    job = st.enqueue(project_id=project.id, kind="k", idempotency_key="i1")
    calls = []

    async def scenario():
        ctx = JobContext(job, st)

        async def work():
            calls.append(1)
            return {"n": len(calls)}

        first = await ctx.step("build", work)
        second = await ctx.step("build", work)      # 模拟重放
        return first, second

    first, second = asyncio.run(scenario())
    assert first == second == {"n": 1}
    assert len(calls) == 1                           # 只真跑了一次
    assert session.query(Step).filter_by(job_id=job.id).count() == 1


def test_worker_runs_handler_and_marks_done(session, project):
    reg = Registry()
    seen = []

    @reg.register("greet")
    async def _h(ctx: JobContext):
        seen.append(ctx.payload["who"])

    _store(session).enqueue(project_id=project.id, kind="greet",
                            idempotency_key="i1", payload={"who": "chen"})
    session.commit()

    w = Worker(lane=BACKGROUND, reg=reg)
    assert asyncio.run(w.run_once()) is True
    assert seen == ["chen"]

    with dbmod.session_scope() as s2:
        assert s2.query(Job).one().state == "done"


def test_worker_unknown_kind_fails_fast(session, project):
    _store(session).enqueue(project_id=project.id, kind="nope", idempotency_key="i1")
    session.commit()
    assert asyncio.run(Worker(lane=BACKGROUND, reg=Registry()).run_once()) is True
    with dbmod.session_scope() as s2:
        j = s2.query(Job).one()
        assert j.state == "failed" and "没有注册" in j.last_error


def test_backoff_grows_then_caps(project):
    """空转要退避，不能一直 2s 打 DB；但有活时不睡。"""
    w = Worker(lane=BACKGROUND)
    delays = [w._sleep_seconds(did_work=False) for _ in range(5)]
    assert delays[0] == pytest.approx(2.0)
    assert delays[1] == pytest.approx(4.0)
    assert delays[-1] == pytest.approx(5.0)          # 封顶
    assert w._sleep_seconds(did_work=True) == 0.0    # 有活不睡

    assert Worker(lane=INTERACTIVE)._sleep_seconds(did_work=False) == pytest.approx(0.2)


def test_enqueue_conflict_does_not_nuke_callers_transaction(session, project):
    """**H10 回归。**

    `Session.rollback()` 回滚的是调用方的整个事务 —— 它会 expunge 事务内所有
    pending 对象、还原所有已修改对象。enqueue 跑在 worker 的事务里，此前已经
    flush 了 req.stage、Step、Event。用 rollback 处理幂等冲突会把这些全抹掉。
    """
    from vplatform.core.models import Requirement, next_requirement_seq

    st = _store(session)
    r = Requirement(project_id=project.id,
                    seq=next_requirement_seq(session, project.id),
                    title="调用方事务里的写", requested_by="u")
    session.add(r)
    session.flush()
    rid = r.id

    st.enqueue(project_id=project.id, kind="k", idempotency_key="dup")
    # 同一个幂等键再来一次 —— 内部会撞 IntegrityError
    again = st.enqueue(project_id=project.id, kind="k", idempotency_key="dup")
    assert again is not None

    # 调用方此前 flush 的东西必须还在
    assert session.get(Requirement, rid) is not None
    assert session.query(Job).filter_by(idempotency_key="dup").count() == 1


# ── 对话不能卡在第一轮 ──────────────────────────────────────────
def test_answering_a_clarify_question_actually_reruns_the_stage(session, project):
    """**step 缓存会把对话卡死在第一轮。**

    第一轮问完问题 → step 记成 done(`awaiting=True`) → 挂起；
    人答完唤醒，`ctx.step` 直接命中缓存又挂一次 —— agent 根本不会被再调，
    问题永远问不完。跟「打回改」那个死胡同是同一个坑。

    真实跑一条需求时暴露的：AI 问了「手续费含不含印花税」，人答了，
    需求原地不动。
    """
    from vplatform.core.models import Message, Requirement, next_requirement_seq
    from vplatform.orchestration.handlers import _talk_round

    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="x", requested_by="chen", stage="clarify")
    session.add(r); session.flush()

    assert _talk_round(session, r.id) == 0
    session.add(Message(project_id=project.id, requirement_id=r.id, role="user",
                        author="chen", body="第一句", stage="clarify"))
    session.flush()
    assert _talk_round(session, r.id) == 1

    # agent 说的话不算轮次 —— 只有人回话才该让缓存失效
    session.add(Message(project_id=project.id, requirement_id=r.id, role="agent",
                        author="ai", body="问题？", stage="clarify"))
    session.flush()
    assert _talk_round(session, r.id) == 1

    session.add(Message(project_id=project.id, requirement_id=r.id, role="user",
                        author="chen", body="答案", stage="clarify"))
    session.flush()
    assert _talk_round(session, r.id) == 2


def test_step_key_changes_only_when_a_person_speaks(session, project):
    """非对话环节的轮次不变，缓存照常生效 —— 重放仍要幂等。"""
    from vplatform.core.models import Message, Requirement, next_requirement_seq
    from vplatform.orchestration.handlers import _talk_round

    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="x", requested_by="chen", stage="implement")
    session.add(r); session.flush()
    before = _talk_round(session, r.id)
    session.add(Message(project_id=project.id, requirement_id=r.id, role="system",
                        author="平台", body="通告", stage="implement"))
    session.flush()
    assert _talk_round(session, r.id) == before, "系统通告不该让缓存失效"


def test_a_dead_job_marks_the_requirement_failed(session, project):
    """**job 重试耗尽了，需求也要跟着标失败。**

    只把 job 置 failed 的话，需求还是 active：看板上显示「在跑」，
    重试接口回「正在跑，不用重试」—— 需求永远卡在那儿，谁也推不动，
    唯一的出路是去数据库改状态。实测走真需求时撞到过。
    """
    import asyncio

    from vplatform.core.models import Requirement, next_requirement_seq
    from vplatform.orchestration.jobs import JobStore
    from vplatform.orchestration.worker import Registry, Worker

    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="x", requested_by="chen", stage="decompose")
    session.add(r); session.flush()
    store = JobStore(session)
    job = store.enqueue(project_id=project.id, kind="boom", requirement_id=r.id,
                        idempotency_key="k-boom", payload={})
    job.attempts = 99                      # 已经到重试上限
    session.commit()

    reg = Registry()

    @reg.register("boom")
    async def _boom(ctx):
        raise RuntimeError("炸了")

    w = Worker(lane=job.lane, reg=reg)
    asyncio.run(w.run_once())
    session.expire_all()
    assert session.get(Requirement, r.id).state == "failed"
