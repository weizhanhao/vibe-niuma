"""端到端：一条需求走完整条流水线，含人工闸门。

这是把 M1(编排) + M6(DAG) + 人工 gate 串起来的验收测试。
"""
import asyncio

import pytest

from vplatform.core import db as dbmod
from vplatform.core.models import (
    Job, Member, Org, Project, Requirement, Task, TaskTouch, next_requirement_seq,
)
from vplatform.orchestration import handlers
from vplatform.orchestration.dag import default_pipeline, load_pipeline
from vplatform.orchestration.handlers import (
    registry, skill_prompt, touch_conflicts, wide_refactor_exempt,
)
from vplatform.orchestration.jobs import JobStore
from vplatform.orchestration.worker import Worker


@pytest.fixture()
def req(session, project):
    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="订单导出支持自定义字段", requested_by="chen", stage="triage")
    session.add(r)
    session.flush()
    JobStore(session).enqueue(project_id=project.id, kind="advance_requirement",
                              requirement_id=r.id,
                              idempotency_key=f"req:{r.id}:triage",
                              payload={"requirement_id": r.id, "stage": "triage"})
    session.commit()
    return r


class _FakeStageRunner:
    """让 12 个环节都「真的做了事」的替身。

    不是为了绕过检查 —— 是为了在**不起容器不烧 token** 的前提下测调度语义。
    每个环节返回 ok=True，于是失败传播、闸门挂起、返工重跑这些逻辑才测得到。
    """


def _fake_caps():
    from vplatform.orchestration import stages as _stages

    class _Ok:
        def __getattr__(self, name):
            async def _call(*a, **kw):
                return _stages.StageOutcome(True, f"fake:{name}")
            return _call

    caps = handlers.Capabilities(workspace=object(), agent=object(),
                                 reviewer=object(), deployer=object(),
                                 host=object())
    _stages.StageRunner = _patch_runner()
    return caps


_ORIG_RUNNER = None


def _patch_runner():
    global _ORIG_RUNNER
    from vplatform.orchestration import stages as _stages
    if _ORIG_RUNNER is None:
        _ORIG_RUNNER = _stages.StageRunner

    class _R(_ORIG_RUNNER):
        def __getattribute__(self, name):
            if name in _stages.DISPATCH.values():
                async def _call(stage, req):
                    return _stages.StageOutcome(True, f"fake:{name}")
                return _call
            return object.__getattribute__(self, name)

    return _R


@pytest.fixture(autouse=True)
def _restore_runner():
    from vplatform.orchestration import stages as _stages
    orig = _stages.StageRunner
    yield
    _stages.StageRunner = orig


def _drain(max_rounds=60):
    """把两条 lane 都跑干，返回实际处理轮数。"""
    async def go():
        rounds = 0
        for _ in range(max_rounds):
            did = False
            for lane in ("interactive", "background"):
                if await Worker(lane=lane, reg=registry).run_once():
                    did = True
                    rounds += 1
            if not did:
                break
        return rounds
    return asyncio.run(go())


def test_empty_capabilities_blocks_instead_of_faking_success(session, project, req):
    """**这个测试取代了原来那个「空壳也能走到 done」的用例。**

    专家审查指出：原用例断言的是"什么都不做也能标记完成"。现在能力缺席时
    流水线必须在第一个需要能力的环节**停住并标 blocked**，
    而不是一路飘到人工审核页让人批准一条零改动的需求。
    """
    handlers.configure(handlers.Capabilities())
    _drain()

    with dbmod.session_scope() as s:
        r = s.get(Requirement, req.id)
        assert r.state == "blocked", f"空能力必须阻断，实际 state={r.state}"
        # triage 是第一个声明 skill 的环节 —— 连它都跑不了，后面更不该走
        assert r.stage == "triage", f"应停在第一个要能力的环节，实际 {r.stage}"


def test_pipeline_advances_and_parks_at_human_gate(session, project, req):
    """能力齐备时需求自动推进，到人工闸门停下 —— **且不占 worker**。"""
    handlers.configure(_fake_caps())
    _drain()

    with dbmod.session_scope() as s:
        r = s.get(Requirement, req.id)
        assert r.stage == "review", f"应停在第一个人工闸门，实际 {r.stage}"
        job = s.query(Job).filter_by(requirement_id=req.id,
                                     state="awaiting_signal").one()
        assert job.locked_by is None       # 挂起不持锁，工位可回收


def test_approval_resumes_to_the_end(session, project, req):
    handlers.configure(_fake_caps())
    _drain()

    # 两个人工闸门：review 和 release
    for _ in range(2):
        with dbmod.session_scope() as s:
            job = s.query(Job).filter_by(requirement_id=req.id,
                                         state="awaiting_signal").first()
            if job is None:
                break
            JobStore(s).signal(job.id, "review_decision",
                               {"decision": "approve", "by": "zhao"})
        _drain()

    with dbmod.session_scope() as s:
        r = s.get(Requirement, req.id)
        assert r.stage == "release" and r.state == "done"


def test_reject_stops_the_requirement(session, project, req):
    handlers.configure(_fake_caps())
    _drain()
    with dbmod.session_scope() as s:
        job = s.query(Job).filter_by(requirement_id=req.id,
                                     state="awaiting_signal").one()
        JobStore(s).signal(job.id, "review_decision", {"decision": "reject"})
    _drain()
    with dbmod.session_scope() as s:
        assert s.get(Requirement, req.id).state == "discarded"


def test_replay_does_not_redo_completed_stages(session, project, req):
    """进程被 kill 重来 —— 已完成的环节要跳过，不重复开工位烧 token。"""
    handlers.configure(_fake_caps())
    _drain()
    with dbmod.session_scope() as s:
        from vplatform.core.models import Step
        before = s.query(Step).count()
        assert before > 0
    _drain()      # 再跑一轮
    with dbmod.session_scope() as s:
        from vplatform.core.models import Step
        assert s.query(Step).count() == before      # 没有新增重复 step


def test_missing_capability_is_reported_not_silently_passed(session, project, req):
    """能力没注入时必须**明说缺什么并阻断**。静默假成功是最难查的一类 bug。"""
    handlers.configure(handlers.Capabilities())
    _drain()
    with dbmod.session_scope() as s:
        from vplatform.core.models import Step
        steps = {st.name: st.output for st in s.query(Step).all()}
    # step 名带对话轮次后缀（`stage:triage:0`）—— 按前缀找，别写死全名，
    # 否则每次改缓存键都要陪着改一遍测试
    def _step(prefix):
        return next((v for k, v in steps.items() if k.startswith(prefix)), None)

    first = _step("stage:triage") or {}
    assert first.get("missing") == ["agent"]
    assert "缺少能力" in first["skipped"]
    # 后面的环节根本不该被执行 —— 阻断意味着停下，不是继续跳过
    assert _step("stage:implement") is None
    assert _step("stage:review") is None


# ── skill 调用约定 ───────────────────────────────────────────────
def test_skill_prompt_names_the_tool_explicitly():
    """照抄 mattpocock 的 invocation 约定：显式说「调用 Skill 工具」，
    而不是丢一个 /name 让模型揣摩。"""
    st = default_pipeline().get("decompose")
    p = skill_prompt(st, context="需求原文……")
    assert 'Call the Skill tool with "to-tickets"' in p
    assert 'Call the Skill tool with "decompose-critic"' in p
    assert "连续 2 轮不过则停止拆分" in p          # D6：不卡人工闸门，降级串行


def test_stage_without_skill_yields_plain_context():
    """没配 skill 就只有语言约定 + 正文，不该凭空多出别的指令。"""
    st = load_pipeline("pipeline:\n  - x: {}").get("x")
    p = skill_prompt(st, context="做点什么")
    assert p.endswith("做点什么")
    assert "Skill tool" not in p
    # 语言约定是全局的：思考要用中文，否则页面上全是英文推理
    assert "用中文思考" in p


# ── touches 冲突前置（§8.3 保险 ①）────────────────────────────────
def _task_with_touches(session, project, req, key, paths, sequence=None):
    t = Task(project_id=project.id, requirement_id=req.id, key=key, title=key,
             sequence=sequence)
    session.add(t); session.flush()
    for p in paths:
        session.add(TaskTouch(project_id=project.id, task_id=t.id, path=p,
                              repo_name="api"))
    session.flush()
    return t


def test_touch_conflict_is_detected_before_merge(session, project, req):
    """**这是把冲突预防前置到调度期的验收** —— 不是等合并期让 AI 收拾。"""
    other = Requirement(project_id=project.id,
                        seq=next_requirement_seq(session, project.id),
                        title="对账单月度汇总", requested_by="zhou", stage="implement")
    session.add(other); session.flush()
    _task_with_touches(session, project, other, "T1", ["app/routers/export.py"])

    hit = touch_conflicts(session, project_id=project.id,
                          paths={"app/routers/export.py"},
                          exclude_requirement=req.id)
    assert hit == {other.id}

    assert touch_conflicts(session, project_id=project.id,
                           paths={"src/pages/Login.tsx"},
                           exclude_requirement=req.id) == set()


def test_finished_requirements_do_not_trigger_conflicts(session, project, req):
    done = Requirement(project_id=project.id,
                       seq=next_requirement_seq(session, project.id),
                       title="已上线的", requested_by="u", state="done")
    session.add(done); session.flush()
    _task_with_touches(session, project, done, "T1", ["app/routers/export.py"])
    assert touch_conflicts(session, project_id=project.id,
                           paths={"app/routers/export.py"}) == set()


def test_wide_refactor_is_exempt_from_conflict_gate(session, project, req):
    """§8.4：wide refactor 的 touches 大面积相交是预期的。
    正确处理不是卡住，是识别成 expand→migrate→contract 序列。"""
    _task_with_touches(session, project, req, "T1", ["a.py", "b.py"], sequence="expand")
    assert wide_refactor_exempt(session, req.id) is True


def test_normal_requirement_is_not_exempt(session, project, req):
    _task_with_touches(session, project, req, "T1", ["a.py"])
    assert wide_refactor_exempt(session, req.id) is False
