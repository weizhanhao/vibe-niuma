"""FastAPI 应用 —— Web 控制台的后端（M4）。

路由按空间 slug 分组，每条都过 project_member 依赖做租户隔离。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vplatform.api.deps import current_user, db, project_member, require_reviewer
from vplatform.api.schemas import (
    ActivityOut, DraftEditIn, EnvOut, FindingOut, IntakeIn, MergeJobOut, MessageIn,
    MessageOut, PreviewOut, ProjectOut, RequirementIn, RequirementOut, ReviewIn,
    TaskOut,
)
from vplatform.core.events import get_bus
from vplatform.core.models import (
    Event, Finding, Job, Member, MergeJob, Message, PortLease, Project, ProjectRepo,
    Requirement, Review, Run, Task, TaskTouch, Workspace, next_requirement_seq,
)
from vplatform.deploy.selfhosted import latest_by_env
from vplatform.orchestration.dag import default_pipeline
from vplatform.orchestration.jobs import INTERACTIVE, JobStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from vplatform.bootstrap import install
    from vplatform.core.db import init_engine

    # API 独立启动时也要建表 —— 之前只有 worker_main 调 create_all，
    # 先起 API 的话表都不存在。
    init_engine(create_all=True)
    install()
    logger.info("vplatform API 启动，装配完成")
    yield
    await get_bus().close()


app = FastAPI(title="vibe-niuma 并行开发调度台", version="0.2.0", lifespan=lifespan)

from vplatform.api.admin import router as admin_router  # noqa: E402
app.include_router(admin_router)


# ── 健康 ─────────────────────────────────────────────────────────
@app.get("/health")
def health(s: Session = Depends(db)) -> dict:
    """**不重复 v1 的错误**：v1 生产上 mysql/llm_proxy/main_demo 三项永远是
    "unknown"，等于没有健康检查。这里每一项都真探，探不到就说探不到 + 原因。
    """
    checks: dict[str, str] = {}
    try:
        s.execute(select(func.count(Project.id)))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["db"] = f"fail: {type(exc).__name__}"

    try:
        pipeline = default_pipeline()
        checks["pipeline"] = f"ok ({len(pipeline.stages)} 环节)"
    except Exception as exc:  # noqa: BLE001
        checks["pipeline"] = f"fail: {exc}"

    stuck = s.execute(
        select(func.count(Job.id)).where(Job.state == "failed")
    ).scalar_one()
    checks["failed_jobs"] = str(stuck)

    status = "ok" if all(not v.startswith("fail") for v in checks.values()) else "degraded"
    return {"status": status, "checks": checks}


# ── 空间 ─────────────────────────────────────────────────────────
@app.get("/projects", response_model=list[ProjectOut])
def list_projects(s: Session = Depends(db), user: str = Depends(current_user)):
    """只列出用户有权限的空间。"""
    rows = s.execute(
        select(Project).join(Member, Member.project_id == Project.id)
        .where(Member.user_id == user)
    ).scalars().all()
    out = []
    for p in rows:
        repos = s.execute(
            select(ProjectRepo.name).where(ProjectRepo.project_id == p.id)
        ).scalars().all()
        active = s.execute(
            select(func.count(Requirement.id)).where(
                Requirement.project_id == p.id, Requirement.state == "active")
        ).scalar_one()
        gate = s.execute(
            select(func.count(Requirement.id)).where(
                Requirement.project_id == p.id, Requirement.stage == "review")
        ).scalar_one()
        out.append(ProjectOut(id=p.id, name=p.name, slug=p.slug,
                              target_branch=p.target_branch, repos=list(repos),
                              quota_parallel_runs=p.quota_parallel_runs,
                              active_requirements=active, awaiting_review=gate))
    return out


@app.get("/projects/{slug}/pipeline")
def get_pipeline(pm=Depends(project_member), s: Session = Depends(db)):
    """流水线是配置不是代码 —— 前端直接渲染这个。"""
    p, _ = pm
    pipeline = _pipeline_for(s, p)
    return {
        "stages": [
            {"key": st.key, "label": st.label, "human_gate": st.is_human_gate,
             "skill": st.skill, "adapter": st.adapter, "env": st.env}
            for st in pipeline.stages
        ],
        "required_skills": sorted(pipeline.required_skills),
        "target_branch": p.target_branch,
    }


# ── 需求 ─────────────────────────────────────────────────────────
def _awaiting_ids(s: Session, req_ids: list[str]) -> set[str]:
    """哪些需求正卡在「AI 问了、人还没答」上。

    一次查完，避免看板上每条需求各发一次查询。"""
    if not req_ids:
        return set()
    return set(s.execute(
        select(Message.requirement_id)
        .where(Message.requirement_id.in_(req_ids),
               Message.role == "agent", Message.awaiting_answer.is_(True))
    ).scalars().all())


def _to_out(s: Session, r: Requirement, *, awaiting: bool | None = None) -> RequirementOut:
    tasks = s.execute(select(Task).where(Task.requirement_id == r.id)
                      .order_by(Task.key)).scalars().all()
    task_out = []
    for t in tasks:
        touches = s.execute(
            select(TaskTouch.path).where(TaskTouch.task_id == t.id)
        ).scalars().all()
        # 最后一次执行的失败原因。任务只显示 "failed" 的话，
        # 用户既不知道哪挂了，也不知道该补什么信息。
        last = s.execute(
            select(Run).where(Run.task_id == t.id)
            .order_by(Run.attempt.desc()).limit(1)
        ).scalars().first()
        task_out.append(TaskOut(id=t.id, key=t.key, title=t.title,
                                repos=list(t.repo_names or []),
                                depends_on=list(t.depends_on or []),
                                touches=list(touches), sequence=t.sequence,
                                state=t.state,
                                fail_reason=(last.fail_reason or "") if last else "",
                                attempts=last.attempt if last else 0))
    return RequirementOut(id=r.id, ref=r.ref, title=r.title, body=r.body,
                          requested_by=r.requested_by, stage=r.stage, state=r.state,
                          contracts=list(r.contracts or []),
                          sequence_kind=r.sequence_kind, tasks=task_out,
                          awaiting_answer=(_awaiting_ids(s, [r.id]) != set()
                                           if awaiting is None else awaiting),
                          created_at=r.created_at)


@app.get("/projects/{slug}/requirements", response_model=list[RequirementOut])
def list_requirements(stage: str | None = Query(default=None),
                      drafts: bool = Query(default=False),
                      pm=Depends(project_member), s: Session = Depends(db)):
    """默认不返回草稿。

    草稿是「还在谈、还没成型」的东西，混进看板会让人以为它在跑。
    要看草稿显式传 `?drafts=true`。
    """
    p, _ = pm
    stmt = select(Requirement).where(Requirement.project_id == p.id)
    stmt = stmt.where(Requirement.state == "draft" if drafts
                      else Requirement.state != "draft")
    if stage:
        stmt = stmt.where(Requirement.stage == stage)
    rows = s.execute(stmt.order_by(Requirement.created_at.desc())).scalars().all()
    waiting = _awaiting_ids(s, [r.id for r in rows])
    return [_to_out(s, r, awaiting=r.id in waiting) for r in rows]


@app.post("/projects/{slug}/requirements", response_model=RequirementOut, status_code=201)
def create_requirement(payload: RequirementIn, pm=Depends(project_member),
                       s: Session = Depends(db), user: str = Depends(current_user)):
    """提需求 —— 用户只管说要什么，剩下平台自己走。"""
    p, _ = pm
    r = Requirement(project_id=p.id, seq=next_requirement_seq(s, p.id),
                    title=payload.title, body=payload.body, requested_by=user,
                    attachments=payload.attachments, stage="triage")
    s.add(r)
    s.flush()
    JobStore(s).enqueue(project_id=p.id, kind="advance_requirement",
                        requirement_id=r.id, lane=INTERACTIVE,
                        idempotency_key=f"req:{r.id}:triage",
                        payload={"requirement_id": r.id, "stage": "triage"})
    return _to_out(s, r)


@app.post("/projects/{slug}/intake", response_model=RequirementOut, status_code=201)
def start_intake(payload: IntakeIn, pm=Depends(project_member),
                 s: Session = Depends(db), user: str = Depends(current_user)):
    """立需求 —— **先谈，不进流程**。

    之前「提需求」是个表单，填完直接进 triage 往下跑。业务员坐下来时
    脑子里往往只有一句「导出太难用了」，表单逼他一次写清楚，
    写不清楚就带着含糊往下走，到人工审核才发现方向错了。
    """
    p, _ = pm
    opening = payload.opening.strip()
    r = Requirement(project_id=p.id, seq=next_requirement_seq(s, p.id),
                    title=opening[:80], body=opening, requested_by=user,
                    stage="intake", state="draft")
    s.add(r)
    s.flush()
    s.add(Message(project_id=p.id, requirement_id=r.id, role="user",
                  author=user, body=opening, stage="intake"))
    JobStore(s).enqueue(project_id=p.id, kind="refine_draft",
                        requirement_id=r.id, lane=INTERACTIVE,
                        idempotency_key=f"draft:{r.id}:0",
                        payload={"requirement_id": r.id})
    return _to_out(s, r)


@app.patch("/projects/{slug}/requirements/{req_id}", response_model=RequirementOut)
def edit_draft(req_id: str, payload: DraftEditIn, pm=Depends(project_member),
               s: Session = Depends(db)):
    """确认之前人可以直接改需求稿 —— AI 写的稿子不一定对。"""
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    if r.state != "draft":
        raise HTTPException(409, "已经进流程了，改需求请在对话里说")
    if payload.title is not None:
        r.title = payload.title.strip() or r.title
    if payload.body is not None:
        r.body = payload.body
    s.flush()
    return _to_out(s, r)


@app.post("/projects/{slug}/requirements/{req_id}/submit",
          response_model=RequirementOut, status_code=201)
def submit_draft(req_id: str, pm=Depends(project_member), s: Session = Depends(db),
                 user: str = Depends(current_user)):
    """确认需求稿 → 正式进流程。"""
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    if r.state != "draft":
        raise HTTPException(409, "这条需求已经在流程里了")
    if not r.title.strip():
        raise HTTPException(422, "需求稿还没有标题")

    r.state, r.stage = "active", "triage"
    s.add(Message(project_id=p.id, requirement_id=req_id, role="system",
                  author=user, body=f"{user} 确认了需求稿，进入流程。", stage="triage"))
    # 草稿阶段挂起的 job 已经没意义了 —— 留着它会在人回话时把需求拽回草稿态
    for j in s.execute(
        select(Job).where(Job.requirement_id == req_id,
                          Job.state == "awaiting_signal")
    ).scalars():
        j.state = "done"
    s.flush()
    JobStore(s).enqueue(project_id=p.id, kind="advance_requirement",
                        requirement_id=req_id, lane=INTERACTIVE,
                        idempotency_key=f"req:{req_id}:triage",
                        payload={"requirement_id": req_id, "stage": "triage"})
    return _to_out(s, r)


@app.get("/projects/{slug}/requirements/{req_id}", response_model=RequirementOut)
def get_requirement(req_id: str, pm=Depends(project_member), s: Session = Depends(db)):
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    return _to_out(s, r)


# ── 复核发现 ─────────────────────────────────────────────────────
@app.get("/projects/{slug}/requirements/{req_id}/findings", response_model=list[FindingOut])
def list_findings(req_id: str, include_dropped: bool = Query(default=False),
                  pm=Depends(project_member), s: Session = Depends(db)):
    p, _ = pm
    run_ids = s.execute(
        select(Run.id).join(Task, Task.id == Run.task_id)
        .where(Task.requirement_id == req_id, Run.project_id == p.id)
    ).scalars().all()
    if not run_ids:
        return []
    stmt = select(Finding).where(Finding.run_id.in_(run_ids))
    if not include_dropped:
        stmt = stmt.where(Finding.kept.is_(True))
    rows = s.execute(stmt).scalars().all()
    return [FindingOut(id=f.id, axis=f.axis, severity=f.severity, category=f.category,
                       path=f.path, start_line=f.start_line, claim=f.claim,
                       failure_scenario=f.failure_scenario, kept=f.kept,
                       confidence=f.confidence, verdict_reason=f.verdict_reason)
            for f in rows]


# ── 对话（澄清 / 续改）────────────────────────────────────────
@app.get("/projects/{slug}/requirements/{req_id}/messages",
         response_model=list[MessageOut])
def list_messages(req_id: str, pm=Depends(project_member), s: Session = Depends(db)):
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    rows = s.execute(
        select(Message).where(Message.requirement_id == req_id)
        .order_by(Message.created_at)
    ).scalars().all()
    return [MessageOut(id=m.id, role=m.role, author=m.author, body=m.body,
                       stage=m.stage, awaiting_answer=m.awaiting_answer,
                       trace=list(m.trace or []),
                       created_at=m.created_at) for m in rows]


@app.post("/projects/{slug}/requirements/{req_id}/messages",
          response_model=MessageOut, status_code=201)
def post_message(req_id: str, payload: MessageIn, pm=Depends(project_member),
                 s: Session = Depends(db), user: str = Depends(current_user)):
    """回答澄清问题，或在任意阶段追加反馈（续改）。

    之前完全没有这个入口 —— 用户提完需求就只能干等，一句话都插不进去。
    """
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    if r.state == "discarded":
        raise HTTPException(409, "需求已关闭")

    # 挂掉的需求收到留言 = 人来接手了，跟着复活。
    # 不改状态的话它会一边真的在重跑，一边在看板上显示「失败」。
    if r.state in ("failed", "blocked"):
        from vplatform.orchestration.handlers import _reset_steps_from
        _reset_steps_from(s, req_id, r.stage)
        r.state = "active"

    body = ("✓ 够了直接干\n" + payload.body) if payload.proceed else payload.body
    msg = Message(project_id=p.id, requirement_id=req_id, role="user",
                  author=user, body=body, stage=r.stage)
    s.add(msg)

    # 把待答的问题标记为已答
    for m in s.execute(
        select(Message).where(Message.requirement_id == req_id,
                              Message.awaiting_answer.is_(True))
    ).scalars():
        m.awaiting_answer = False
    s.flush()

    # 草稿走「立需求」那条线 —— 它还没进流程，不能拿流水线那套推它。
    if r.state == "draft":
        store = JobStore(s)
        parked = s.execute(
            select(Job).where(Job.requirement_id == req_id,
                              Job.state == "awaiting_signal")
        ).scalars().first()
        if parked is not None:
            store.signal(parked.id, "user_message", {"by": user})
        else:
            n = s.execute(
                select(func.count(Message.id)).where(
                    Message.requirement_id == req_id, Message.role == "user")
            ).scalar_one()
            store.enqueue(project_id=p.id, kind="refine_draft",
                          requirement_id=req_id, lane=INTERACTIVE,
                          idempotency_key=f"draft:{req_id}:{n}",
                          payload={"requirement_id": req_id})
        return MessageOut(id=msg.id, role=msg.role, author=msg.author, body=msg.body,
                          stage=msg.stage, awaiting_answer=False,
                          created_at=msg.created_at)

    # **停在人工闸门上的需求，一句留言不该把闸门叫醒。**
    # signal() 会把 job 从 awaiting_signal 拉回 pending；闸门 handler 要的是
    # review_decision，拿不到就再挂一次。这中间有个窗口：审核人刚评论完就点
    # 「通过」，submit_review 找不到 awaiting_signal 的 job，直接 409。
    # 闸门上的留言就是留言，记下来即可。
    stage = _pipeline_for(s, p).get(r.stage) if _has_stage(s, p, r.stage) else None
    if stage is not None and stage.is_human_gate:
        return MessageOut(id=msg.id, role=msg.role, author=msg.author, body=msg.body,
                          stage=msg.stage, awaiting_answer=False,
                          created_at=msg.created_at)

    # 唤醒挂起的 job；没挂起就重新入队当前环节（续改路径）
    store = JobStore(s)
    parked = s.execute(
        select(Job).where(Job.requirement_id == req_id,
                          Job.state == "awaiting_signal")
    ).scalars().first()
    if parked is not None:
        store.signal(parked.id, "user_message", {"by": user})
    else:
        n = s.execute(
            select(func.count(Message.id)).where(Message.requirement_id == req_id,
                                                 Message.role == "user")
        ).scalar_one()
        store.enqueue(project_id=p.id, kind="advance_requirement",
                      requirement_id=req_id, lane=INTERACTIVE,
                      idempotency_key=f"req:{req_id}:{r.stage}:msg{n}",
                      payload={"requirement_id": req_id, "stage": r.stage})
    return MessageOut(id=msg.id, role=msg.role, author=msg.author, body=msg.body,
                      stage=msg.stage, awaiting_answer=False,
                      created_at=msg.created_at)


# ── 重试 ─────────────────────────────────────────────────────────
@app.post("/projects/{slug}/requirements/{req_id}/retry", status_code=201)
def retry_requirement(req_id: str, pm=Depends(project_member),
                      s: Session = Depends(db), user: str = Depends(current_user)):
    """把卡住的需求从当前环节重开。

    环节失败会把需求置成 failed/blocked 后**就地停住** —— 之前没有任何
    重开入口，一条需求挂了就永久躺在看板上，只能去数据库里改状态。
    """
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    if r.state == "discarded":
        raise HTTPException(409, "需求已关闭，不能重试")
    if r.state == "active":
        raise HTTPException(409, "需求正在跑，不用重试")

    from vplatform.orchestration.handlers import _reset_steps_from

    n = 1 + s.execute(
        select(func.count(Job.id)).where(
            Job.requirement_id == req_id,
            Job.idempotency_key.like(f"req:{req_id}:{r.stage}:retry%"))
    ).scalar_one()
    # **必须清 step 缓存。** 不清的话新 job 会命中上一轮那条 done 的 step，
    # 「重试」就成了什么都不做，还报成功 —— 比不给按钮更糟。
    _reset_steps_from(s, req_id, r.stage)
    r.state = "active"
    s.add(Message(project_id=p.id, requirement_id=req_id, role="system",
                  author=user, body=f"{user} 从「{r.stage}」环节重开了这条需求。",
                  stage=r.stage))
    s.flush()
    JobStore(s).enqueue(project_id=p.id, kind="advance_requirement",
                        requirement_id=req_id, lane=INTERACTIVE,
                        idempotency_key=f"req:{req_id}:{r.stage}:retry{n}",
                        payload={"requirement_id": req_id, "stage": r.stage})
    return {"ok": True, "stage": r.stage, "attempt": n}


# ── 审核（人工闸门）──────────────────────────────────────────────
@app.post("/projects/{slug}/requirements/{req_id}/review", status_code=201)
def submit_review(req_id: str, payload: ReviewIn, pm=Depends(require_reviewer),
                  s: Session = Depends(db), user: str = Depends(current_user)):
    """审核决定 → 投递信号唤醒挂起的 job。

    挂起期间不占 worker（§7.3 ③），信号一到就拉回交互 lane，延迟上限 200ms。
    """
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    # **不能硬编码 "review"** —— release 也是人工闸门，写死 review 会让需求
    # 永远卡在 release 上，任何 HTTP 接口都批不了。
    stage = _pipeline_for(s, p).get(r.stage) if _has_stage(s, p, r.stage) else None
    if stage is None or not stage.is_human_gate:
        raise HTTPException(409, f"需求当前在 {r.stage} 环节，不是人工闸门")

    s.add(Review(project_id=p.id, requirement_id=req_id, reviewer=user,
                 decision=payload.decision, comment=payload.comment))
    job = s.execute(
        select(Job).where(Job.requirement_id == req_id,
                          Job.state == "awaiting_signal")
    ).scalars().first()
    if job is None:
        raise HTTPException(409, "没有等待审核信号的任务")
    JobStore(s).signal(job.id, "review_decision",
                       {"decision": payload.decision, "by": user})
    return {"ok": True, "decision": payload.decision}


def _pipeline_for(s: Session, project: Project):
    from vplatform.orchestration.handlers import caps_for
    return caps_for(s, project.id).pipe()


def _has_stage(s: Session, project: Project, key: str) -> bool:
    return any(st.key == key for st in _pipeline_for(s, project).stages)


# ── 合并队列 ─────────────────────────────────────────────────────
@app.get("/projects/{slug}/merge-queue", response_model=list[MergeJobOut])
def merge_queue(pm=Depends(project_member), s: Session = Depends(db)):
    p, _ = pm
    rows = s.execute(
        select(MergeJob, Requirement.seq)
        .join(Requirement, Requirement.id == MergeJob.requirement_id)
        .where(MergeJob.project_id == p.id,
               MergeJob.state.notin_(("merged", "rejected")))
        .order_by(MergeJob.repo_name, MergeJob.position)
    ).all()
    return [MergeJobOut(id=j.id, requirement_ref=f"R-{seq}", repo_name=j.repo_name,
                        position=j.position, state=j.state,
                        conflict_ladder=list(j.conflict_ladder or []))
            for j, seq in rows]


# ── 环境（三层）──────────────────────────────────────────────────
@app.get("/projects/{slug}/environments", response_model=list[EnvOut])
def environments(pm=Depends(project_member)):
    p, _ = pm
    latest = latest_by_env(p.id)
    out = []
    for env in ("preview", "test", "prod"):
        st = latest.get(env)
        out.append(EnvOut(env=env, state=st.state if st else "never",
                          url=st.external_url if st else None,
                          finished_at=st.finished_at if st else None))
    return out


# ── 预览 ─────────────────────────────────────────────────────────
@app.get("/projects/{slug}/requirements/{req_id}/previews",
         response_model=list[PreviewOut])
def previews(req_id: str, pm=Depends(project_member), s: Session = Depends(db)):
    """这条需求的预览地址 —— 每个并行分支一个。

    `preview` 环节算出了这些地址，但只写进了事件里，界面上从来没渲染过：
    「业务员自己点开看效果」这个卖点等于不存在。

    **只在工位还活着时给链接。** 工位一回收端口就没人监听了，
    给一个点开必然报错的链接比不给更糟。
    """
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    rows = s.execute(
        select(Run, Task.key, PortLease.port)
        .join(Task, Task.id == Run.task_id)
        .join(Workspace, Workspace.run_id == Run.id)
        .join(PortLease, PortLease.workspace_id == Run.id)
        .where(Task.requirement_id == req_id, Run.project_id == p.id,
               Workspace.state == "ready")
    ).all()
    host = os.environ.get("VP_PREVIEW_HOST", "127.0.0.1")
    return [PreviewOut(branch=run.branch, task_key=key, url=f"http://{host}:{port}")
            for run, key, port in rows]


# ── 活动历史 ─────────────────────────────────────────────────────
@app.get("/projects/{slug}/requirements/{req_id}/activity",
         response_model=list[ActivityOut])
def activity(req_id: str, limit: int = Query(default=200, le=1000),
             pm=Depends(project_member), s: Session = Depends(db)):
    """这条需求身上发生过什么。

    SSE 只推「从现在起」的事件：中途打开页面的人、以及需求失败后
    回来看的人，之前什么都看不到。这个接口补历史。
    """
    p, _ = pm
    r = s.get(Requirement, req_id)
    if r is None or r.project_id != p.id:
        raise HTTPException(404, "需求不存在")
    rows = s.execute(
        select(Event).where(Event.project_id == p.id,
                            Event.stream == f"req:{req_id}")
        .order_by(Event.id.desc()).limit(limit)
    ).scalars().all()
    out = []
    for e in reversed(rows):
        pl = e.payload or {}
        # 失败原因可能在 reason / error / detail 里，取到哪个算哪个
        detail = str(pl.get("reason") or pl.get("error") or pl.get("detail")
                     or pl.get("message") or "")
        out.append(ActivityOut(id=e.id, kind=e.kind, stage=str(pl.get("stage") or ""),
                               state=str(pl.get("state") or ""), detail=detail[:2000],
                               created_at=e.created_at))
    return out


# ── SSE ──────────────────────────────────────────────────────────
@app.get("/projects/{slug}/requirements/{req_id}/events")
async def stream_events(req_id: str, request: Request,
                        last_event_id: int = Query(default=0, alias="lastEventId"),
                        pm=Depends(project_member),
                        s_: Session = Depends(db)):
    """实时事件流。带 lastEventId 回来即可断线续传（§13）。

    **进流之前必须先把数据库事务放掉。**
    FastAPI 的 `db` 依赖是 `with session_scope()`，它活到响应结束为止 ——
    对普通接口是几毫秒，对 SSE 就是**整条长连接**（几分钟）。
    鉴权时写过 `api_tokens.last_used_at`，那把行锁就一直攥着，
    后面每个请求（页面每 3 秒轮询一次）都排队等到
    `Lock wait timeout exceeded`，前端显示红色的 Internal Server Error。
    实测用户反复撞到。
    """
    p, _ = pm
    project_id = p.id                   # 事务马上要提交，先把要用的值取出来
    # FastAPI 会缓存 `db` 依赖，所以 s_ 跟 project_member 用的是同一个
    # session —— 提交它就把鉴权时拿的那把行锁放掉了。
    s_.commit()
    bus = get_bus()

    async def gen():
        agen = bus.subscribe(project_id=project_id, stream=f"req:{req_id}",
                             last_event_id=last_event_id)
        try:
            while True:
                try:
                    # 定期醒来查断连 —— 之前只在收到事件后才检查，
                    # 安静的流上客户端断开后协程永远阻塞在 q.get()，
                    # 订阅队列不回收。
                    ev = await asyncio.wait_for(agen.__anext__(), timeout=15)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keep-alive\n\n"
                    continue
                except StopAsyncIteration:
                    break
                if await request.is_disconnected():
                    break
                yield ev.sse()
        except asyncio.CancelledError:
            raise
        finally:
            await agen.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
