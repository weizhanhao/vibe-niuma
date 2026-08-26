"""API 测试 —— 重点在租户隔离与人工闸门，那是最容易出安全/正确性问题的地方。"""
import pytest
from fastapi.testclient import TestClient

from vplatform.api.main import app
from vplatform.core.models import (
    Job, Member, Org, Project, ProjectRepo, Requirement, Review, next_requirement_seq,
)
from vplatform.orchestration.jobs import JobStore


@pytest.fixture()
def client(engine):
    return TestClient(app)


@pytest.fixture()
def world(session):
    """两个空间、三个用户 —— 隔离必须在真有第二个租户时才测得出来。"""
    org = Org(name="acme"); session.add(org); session.flush()
    mc = Project(org_id=org.id, name="商户中台", slug="mc")
    wms = Project(org_id=org.id, name="仓配", slug="wms")
    session.add_all([mc, wms]); session.flush()
    session.add_all([
        ProjectRepo(project_id=mc.id, name="merchant-web", url="https://x/web.git"),
        ProjectRepo(project_id=mc.id, name="merchant-api", url="https://x/api.git"),
        Member(project_id=mc.id, user_id="chen", role="requester"),
        Member(project_id=mc.id, user_id="zhao", role="reviewer"),
        Member(project_id=wms.id, user_id="sun", role="requester"),
    ])
    session.commit()
    return {"mc": mc, "wms": wms}


def H(user):
    return {"X-User": user}


# ── 鉴权 / 隔离 ──────────────────────────────────────────────────
def test_anonymous_is_rejected(client, world):
    assert client.get("/projects").status_code == 401


def test_user_only_sees_own_projects(client, world):
    r = client.get("/projects", headers=H("chen"))
    assert r.status_code == 200
    assert [p["slug"] for p in r.json()] == ["mc"]
    assert [p["slug"] for p in client.get("/projects", headers=H("sun")).json()] == ["wms"]


def test_non_member_cannot_reach_other_space(client, world):
    """**租户隔离的核心断言**：sun 不是 mc 的成员，一律 403。"""
    for path in ("/projects/mc/requirements", "/projects/mc/merge-queue",
                 "/projects/mc/environments", "/projects/mc/pipeline"):
        assert client.get(path, headers=H("sun")).status_code == 403, path


def test_unknown_space_is_404_not_403(client, world):
    assert client.get("/projects/nope/requirements", headers=H("chen")).status_code == 404


# ── 提需求 ───────────────────────────────────────────────────────
def test_create_requirement_enqueues_job(client, world, session):
    r = client.post("/projects/mc/requirements", headers=H("chen"),
                    json={"title": "订单导出支持自定义字段", "body": "财务每月手工加工"})
    assert r.status_code == 201
    body = r.json()
    assert body["ref"] == "R-1" and body["stage"] == "triage"
    assert body["requested_by"] == "chen"

    session.expire_all()
    job = session.query(Job).filter_by(requirement_id=body["id"]).one()
    assert job.kind == "advance_requirement" and job.lane == "interactive"


def test_requirement_numbering_is_per_space(client, world):
    a = client.post("/projects/mc/requirements", headers=H("chen"),
                    json={"title": "A"}).json()
    b = client.post("/projects/wms/requirements", headers=H("sun"),
                    json={"title": "B"}).json()
    assert a["ref"] == "R-1" and b["ref"] == "R-1"      # 各空间独立编号


def test_empty_title_rejected(client, world):
    assert client.post("/projects/mc/requirements", headers=H("chen"),
                       json={"title": ""}).status_code == 422


def test_cannot_read_requirement_of_another_space(client, world):
    made = client.post("/projects/wms/requirements", headers=H("sun"),
                       json={"title": "别人的"}).json()
    # chen 是 mc 的成员，但这条需求属于 wms —— 不能靠猜 id 读到
    r = client.get(f"/projects/mc/requirements/{made['id']}", headers=H("chen"))
    assert r.status_code == 404


# ── 人工闸门 ─────────────────────────────────────────────────────
def _park_at_review(session, project, title="待审"):
    req = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                      title=title, requested_by="chen", stage="review")
    session.add(req); session.flush()
    st = JobStore(session)
    job = st.enqueue(project_id=project.id, kind="advance_requirement",
                     requirement_id=req.id, idempotency_key=f"g:{req.id}")
    st.claim(worker_id="w")
    st.park(job)
    session.commit()
    return req, job


def test_reviewer_can_approve_and_signal_wakes_job(client, world, session):
    req, job = _park_at_review(session, world["mc"])
    r = client.post(f"/projects/mc/requirements/{req.id}/review", headers=H("zhao"),
                    json={"decision": "approve", "comment": "看着不错"})
    assert r.status_code == 201

    session.expire_all()
    assert session.query(Review).one().decision == "approve"
    j = session.get(Job, job.id)
    assert j.state == "pending"                 # 被唤醒
    assert j.lane == "interactive"              # 人在等 → 升 lane


def test_requester_cannot_approve(client, world, session):
    """chen 是 requester 不是 reviewer —— 自己的需求不能自己批。"""
    req, _ = _park_at_review(session, world["mc"])
    r = client.post(f"/projects/mc/requirements/{req.id}/review", headers=H("chen"),
                    json={"decision": "approve"})
    assert r.status_code == 403


def test_review_on_wrong_stage_is_409(client, world, session):
    req = Requirement(project_id=world["mc"].id,
                      seq=next_requirement_seq(session, world["mc"].id),
                      title="还在开发", requested_by="chen", stage="implement")
    session.add(req); session.commit()
    r = client.post(f"/projects/mc/requirements/{req.id}/review", headers=H("zhao"),
                    json={"decision": "approve"})
    assert r.status_code == 409


def test_invalid_decision_rejected(client, world, session):
    req, _ = _park_at_review(session, world["mc"])
    assert client.post(f"/projects/mc/requirements/{req.id}/review", headers=H("zhao"),
                       json={"decision": "maybe"}).status_code == 422


# ── 其它 ─────────────────────────────────────────────────────────
def test_pipeline_is_config_driven(client, world):
    body = client.get("/projects/mc/pipeline", headers=H("chen")).json()
    keys = [s["key"] for s in body["stages"]]
    assert keys[0] == "triage" and keys[-1] == "release"
    assert [s["key"] for s in body["stages"] if s["human_gate"]] == ["review", "release"]
    assert "to-tickets" in body["required_skills"]


def test_environments_reports_never_when_no_deploy(client, world):
    envs = client.get("/projects/mc/environments", headers=H("chen")).json()
    assert [e["env"] for e in envs] == ["preview", "test", "prod"]
    assert all(e["state"] == "never" for e in envs)


def test_health_reports_real_checks_not_unknown(client, world):
    """v1 生产上三项永远是 'unknown'，等于没有健康检查。这里必须真探。"""
    body = client.get("/health").json()
    assert body["status"] in ("ok", "degraded")
    assert body["checks"]["db"] == "ok"
    assert "unknown" not in str(body["checks"]).lower()
    assert body["checks"]["pipeline"].startswith("ok")


def test_findings_hide_dropped_by_default(client, world, session):
    req = Requirement(project_id=world["mc"].id,
                      seq=next_requirement_seq(session, world["mc"].id),
                      title="x", requested_by="chen")
    session.add(req); session.commit()
    r = client.get(f"/projects/mc/requirements/{req.id}/findings", headers=H("chen"))
    assert r.status_code == 200 and r.json() == []


# ── 认证 ─────────────────────────────────────────────────────────
def test_x_user_is_rejected_outside_dev_mode(client, world, monkeypatch):
    """**这是安全边界**：X-User 只是开发便捷入口。
    生产上没关掉的话，任何人加个头就能冒充任意用户。"""
    monkeypatch.delenv("VP_DEV_AUTH", raising=False)
    r = client.get("/projects", headers={"X-User": "chen"})
    assert r.status_code == 401
    assert "开发模式" in r.json()["detail"]


def test_bearer_token_authenticates(client, world, session, monkeypatch):
    from vplatform.api.auth import issue_token

    monkeypatch.delenv("VP_DEV_AUTH", raising=False)
    raw = issue_token(session, user_id="chen", display_name="陈曦")
    session.commit()

    r = client.get("/projects", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert [p["slug"] for p in r.json()] == ["mc"]


def test_token_is_stored_hashed_only(session):
    """泄库不能拿到可用凭证。"""
    from vplatform.api.auth import ApiToken, issue_token

    raw = issue_token(session, user_id="chen")
    session.flush()
    row = session.query(ApiToken).one()
    assert row.token_hash != raw
    assert raw not in row.token_hash
    assert len(row.token_hash) == 64          # sha256 hex


def test_bad_token_rejected(client, world, monkeypatch):
    monkeypatch.delenv("VP_DEV_AUTH", raising=False)
    assert client.get("/projects",
                      headers={"Authorization": "Bearer vp_nope"}).status_code == 401


# ── 需求对话（澄清 / 续改）──────────────────────────────────────
def _req(session, project, stage="clarify", user="chen"):
    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="导出加字段", body="", requested_by=user, stage=stage)
    session.add(r); session.commit()
    return r


def test_posting_a_message_wakes_the_parked_job(client, world, session):
    """澄清挂起时，用户回话必须能把它拉回来 —— 否则需求永远卡在那。"""
    mc = world["mc"]
    r = _req(session, mc)
    store = JobStore(session)
    job = store.enqueue(project_id=mc.id, kind="advance_requirement",
                        requirement_id=r.id, lane="interactive",
                        idempotency_key=f"req:{r.id}:clarify",
                        payload={"requirement_id": r.id, "stage": "clarify"})
    from vplatform.core.models import Message
    session.add(Message(project_id=mc.id, requirement_id=r.id, role="agent",
                        author="ai", body="是每人一套还是全公司一套？",
                        stage="clarify", awaiting_answer=True))
    store.park(job)
    session.commit()

    resp = client.post(f"/projects/mc/requirements/{r.id}/messages",
                       json={"body": "每人一套"}, headers=H("chen"))
    assert resp.status_code == 201
    session.expire_all()
    assert session.get(Job, job.id).state != "awaiting_signal"
    # 问题已被认领，界面上不该继续显示「等你回答」
    got = client.get(f"/projects/mc/requirements/{r.id}", headers=H("chen")).json()
    assert got["awaiting_answer"] is False


def test_message_without_parked_job_reenqueues_current_stage(client, world, session):
    """续改：需求正在跑，用户追加一句话 —— 也得让它重跑当前环节。"""
    mc = world["mc"]
    r = _req(session, mc, stage="implement")
    resp = client.post(f"/projects/mc/requirements/{r.id}/messages",
                       json={"body": "字段顺序按用户配置来"}, headers=H("chen"))
    assert resp.status_code == 201
    jobs = session.query(Job).filter(Job.requirement_id == r.id).all()
    assert any(j.payload.get("stage") == "implement" for j in jobs)


def test_two_messages_do_not_collide_on_idempotency_key(client, world, session):
    """连发两条不能被幂等键吃掉第二条 —— 那会让用户以为平台没收到。"""
    mc = world["mc"]
    r = _req(session, mc, stage="implement")
    for body in ("第一条", "第二条"):
        assert client.post(f"/projects/mc/requirements/{r.id}/messages",
                           json={"body": body}, headers=H("chen")).status_code == 201
    keys = {j.idempotency_key for j in
            session.query(Job).filter(Job.requirement_id == r.id).all()}
    assert len(keys) == 2


def test_awaiting_answer_shows_up_on_the_board(client, world, session):
    mc = world["mc"]
    r = _req(session, mc)
    from vplatform.core.models import Message
    session.add(Message(project_id=mc.id, requirement_id=r.id, role="agent",
                        author="ai", body="问题？", stage="clarify",
                        awaiting_answer=True))
    session.commit()
    rows = client.get("/projects/mc/requirements", headers=H("chen")).json()
    assert [x["awaiting_answer"] for x in rows if x["id"] == r.id] == [True]


def test_messages_are_tenant_isolated(client, world, session):
    r = _req(session, world["mc"])
    assert client.get(f"/projects/mc/requirements/{r.id}/messages",
                      headers=H("sun")).status_code in (403, 404)


def test_cannot_talk_to_a_discarded_requirement(client, world, session):
    r = _req(session, world["mc"])
    r.state = "discarded"; session.commit()
    assert client.post(f"/projects/mc/requirements/{r.id}/messages",
                       json={"body": "喂"}, headers=H("chen")).status_code == 409


# ── 活动历史 ────────────────────────────────────────────────────
def test_activity_replays_what_already_happened(client, world, session):
    """中途打开页面的人也要看得见之前发生了什么，不能只有实时流。"""
    from vplatform.core.events import get_bus
    mc = world["mc"]
    r = _req(session, mc, stage="verify")
    bus = get_bus()
    bus.record(session, project_id=mc.id, stream=f"req:{r.id}", kind="status",
               payload={"stage": "implement", "state": "done"})
    bus.record(session, project_id=mc.id, stream=f"req:{r.id}", kind="status",
               payload={"stage": "verify", "state": "failed", "reason": "3 个用例挂了"})
    session.commit()

    rows = client.get(f"/projects/mc/requirements/{r.id}/activity",
                      headers=H("chen")).json()
    assert [x["stage"] for x in rows] == ["implement", "verify"]   # 时间正序
    assert rows[-1]["detail"] == "3 个用例挂了"


def test_activity_does_not_leak_across_requirements(client, world, session):
    from vplatform.core.events import get_bus
    mc = world["mc"]
    a, b = _req(session, mc), _req(session, mc)
    get_bus().record(session, project_id=mc.id, stream=f"req:{a.id}", kind="status",
                     payload={"stage": "verify", "state": "failed"})
    session.commit()
    assert client.get(f"/projects/mc/requirements/{b.id}/activity",
                      headers=H("chen")).json() == []


def test_a_comment_at_a_human_gate_does_not_unpark_the_review(client, world, session):
    """审核人先评论再点「通过」不能报 409。

    signal() 会把闸门 job 从 awaiting_signal 拉回 pending，闸门 handler 拿不到
    review_decision 又会重新挂起 —— 这中间的窗口里，submit_review 找不到
    等信号的 job，直接 409「没有等待审核信号的任务」。
    """
    mc = world["mc"]
    r = _req(session, mc, stage="review")
    store = JobStore(session)
    job = store.enqueue(project_id=mc.id, kind="advance_requirement",
                        requirement_id=r.id, lane="interactive",
                        idempotency_key=f"req:{r.id}:review",
                        payload={"requirement_id": r.id, "stage": "review"})
    store.park(job)
    session.commit()

    assert client.post(f"/projects/mc/requirements/{r.id}/messages",
                       json={"body": "这块逻辑我再看看"},
                       headers=H("zhao")).status_code == 201
    session.expire_all()
    assert session.get(Job, job.id).state == "awaiting_signal", "闸门被留言叫醒了"

    assert client.post(f"/projects/mc/requirements/{r.id}/review",
                       json={"decision": "approve"},
                       headers=H("zhao")).status_code == 201


def test_a_comment_at_a_gate_creates_no_duplicate_job(client, world, session):
    """闸门上留言也不该凭空多出一个推进 job —— 那会让闸门批两次。"""
    mc = world["mc"]
    r = _req(session, mc, stage="review")
    before = session.query(Job).filter(Job.requirement_id == r.id).count()
    client.post(f"/projects/mc/requirements/{r.id}/messages",
                json={"body": "顺手问一句"}, headers=H("chen"))
    assert session.query(Job).filter(Job.requirement_id == r.id).count() == before


# ── 卡住的需求要能重开 ──────────────────────────────────────────
def test_failed_requirement_can_be_retried(client, world, session):
    """环节失败会把需求置成 failed 后就地停住。

    之前没有任何重开入口 —— 一条需求挂了就永久躺在看板上。
    """
    mc = world["mc"]
    r = _req(session, mc, stage="verify")
    r.state = "failed"; session.commit()

    resp = client.post(f"/projects/mc/requirements/{r.id}/retry", headers=H("chen"))
    assert resp.status_code == 201 and resp.json()["stage"] == "verify"
    session.expire_all()
    assert session.get(Requirement, r.id).state == "active"
    assert any(j.payload.get("stage") == "verify"
               for j in session.query(Job).filter(Job.requirement_id == r.id))


def test_retry_clears_the_step_cache(client, world, session):
    """不清 step 缓存的话新 job 会命中上一轮 done 的 step，
    「重试」变成什么都不做还报成功 —— 比不给按钮更糟。"""
    from vplatform.core.models import Step
    mc = world["mc"]
    r = _req(session, mc, stage="verify")
    r.state = "failed"
    store = JobStore(session)
    job = store.enqueue(project_id=mc.id, kind="advance_requirement",
                        requirement_id=r.id,
                        idempotency_key=f"req:{r.id}:verify",
                        payload={"requirement_id": r.id, "stage": "verify"})
    session.add(Step(project_id=mc.id, job_id=job.id, name="stage:verify",
                     seq=1, state="done", output={"ok": True}))
    session.commit()

    client.post(f"/projects/mc/requirements/{r.id}/retry", headers=H("chen"))
    session.expire_all()
    assert session.query(Step).filter(Step.job_id == job.id).count() == 0


def test_retry_twice_gets_two_jobs(client, world, session):
    """幂等键必须带轮次，否则第二次重试命中第一次那条 job，静默无效。"""
    mc = world["mc"]
    r = _req(session, mc, stage="verify")
    for _ in range(2):
        # expire 一下：上一轮接口把 state 写成了 active，我们这边的身份映射
        # 还记着 "failed"，不刷新的话赋值不产生 UPDATE，commit 成空操作
        session.expire_all()
        session.get(Requirement, r.id).state = "failed"
        session.commit()
        assert client.post(f"/projects/mc/requirements/{r.id}/retry",
                           headers=H("chen")).status_code == 201
    keys = {j.idempotency_key for j in
            session.query(Job).filter(Job.requirement_id == r.id)}
    assert len([k for k in keys if ":retry" in k]) == 2


def test_active_requirement_is_not_retryable(client, world, session):
    r = _req(session, world["mc"], stage="implement")
    assert client.post(f"/projects/mc/requirements/{r.id}/retry",
                       headers=H("chen")).status_code == 409


def test_discarded_requirement_is_not_retryable(client, world, session):
    r = _req(session, world["mc"])
    r.state = "discarded"; session.commit()
    assert client.post(f"/projects/mc/requirements/{r.id}/retry",
                       headers=H("chen")).status_code == 409


def test_a_message_revives_a_failed_requirement(client, world, session):
    """挂掉的需求收到留言 = 人来接手了。不复活的话它会一边真在重跑，
    一边在看板上显示「失败」。"""
    mc = world["mc"]
    r = _req(session, mc, stage="implement")
    r.state = "failed"; session.commit()
    client.post(f"/projects/mc/requirements/{r.id}/messages",
                json={"body": "改用 orders_v2 表"}, headers=H("chen"))
    session.expire_all()
    assert session.get(Requirement, r.id).state == "active"


# ── 预览地址 ────────────────────────────────────────────────────
def _run_with_preview(session, project, req, *, ws_state="ready", port=5101):
    from vplatform.core.models import PortLease, Run, Task, Workspace
    from datetime import datetime, timedelta
    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    run = Run(project_id=project.id, task_id=t.id, branch="cr/1-t1", state="done")
    session.add(run); session.flush()
    session.add_all([
        Workspace(project_id=project.id, run_id=run.id, path="/tmp/ws",
                  state=ws_state),
        PortLease(project_id=project.id, port=port, workspace_id=run.id,
                  expires_at=datetime.utcnow() + timedelta(hours=1)),
    ])
    session.commit()
    return run


def test_preview_urls_are_exposed(client, world, session):
    """`preview` 环节算出的地址之前只写进事件，界面上从来没渲染过 ——
    「业务员自己点开看效果」这个卖点等于不存在。"""
    mc = world["mc"]
    r = _req(session, mc, stage="preview")
    _run_with_preview(session, mc, r)
    rows = client.get(f"/projects/mc/requirements/{r.id}/previews",
                      headers=H("chen")).json()
    assert rows == [{"branch": "cr/1-t1", "task_key": "T1",
                     "url": "http://127.0.0.1:5101"}]


def test_no_preview_link_once_the_workspace_is_gone(client, world, session):
    """工位一回收端口就没人监听 —— 给一个点开必然报错的链接比不给更糟。"""
    mc = world["mc"]
    r = _req(session, mc, stage="review")
    _run_with_preview(session, mc, r, ws_state="released")
    assert client.get(f"/projects/mc/requirements/{r.id}/previews",
                      headers=H("chen")).json() == []


def test_previews_are_tenant_isolated(client, world, session):
    r = _req(session, world["mc"], stage="preview")
    _run_with_preview(session, world["mc"], r)
    assert client.get(f"/projects/mc/requirements/{r.id}/previews",
                      headers=H("sun")).status_code in (403, 404)


# ── 立需求：先谈，谈成型再进流程 ────────────────────────────────
def test_intake_creates_a_draft_that_is_not_in_the_pipeline(client, world, session):
    """**提需求不该是个表单。**

    业务员坐下来时脑子里往往只有一句「导出太难用了」。表单逼他一次写清楚，
    写不清楚就带着含糊往下走，到人工审核才发现方向错了。
    """
    r = client.post("/projects/mc/intake",
                    json={"opening": "订单导出太难用了"}, headers=H("chen"))
    assert r.status_code == 201
    d = r.json()
    assert d["state"] == "draft" and d["stage"] == "intake"

    jobs = session.query(Job).filter(Job.requirement_id == d["id"]).all()
    assert [j.kind for j in jobs] == ["refine_draft"], "草稿不该进流水线"


def test_drafts_stay_off_the_board(client, world, session):
    """谈到一半的东西混进看板，会让人以为它在跑。"""
    d = client.post("/projects/mc/intake", json={"opening": "随便说说"},
                    headers=H("chen")).json()
    board = client.get("/projects/mc/requirements", headers=H("chen")).json()
    assert d["id"] not in [x["id"] for x in board]
    drafts = client.get("/projects/mc/requirements?drafts=true",
                        headers=H("chen")).json()
    assert [x["id"] for x in drafts] == [d["id"]]


def test_the_opening_line_is_kept_as_the_first_message(client, world, session):
    d = client.post("/projects/mc/intake", json={"opening": "导出太难用了"},
                    headers=H("chen")).json()
    msgs = client.get(f"/projects/mc/requirements/{d['id']}/messages",
                      headers=H("chen")).json()
    assert [(m["role"], m["body"]) for m in msgs] == [("user", "导出太难用了")]


def test_talking_to_a_draft_does_not_start_the_pipeline(client, world, session):
    """草稿阶段回话只能推进「立需求」，不能拿流水线那套推它。"""
    d = client.post("/projects/mc/intake", json={"opening": "导出"},
                    headers=H("chen")).json()
    client.post(f"/projects/mc/requirements/{d['id']}/messages",
                json={"body": "每人一套配置"}, headers=H("chen"))
    kinds = {j.kind for j in session.query(Job).filter(Job.requirement_id == d["id"])}
    assert kinds == {"refine_draft"}


def test_the_person_can_edit_the_draft_before_confirming(client, world, session):
    """AI 写的稿子不一定对 —— 确认之前必须能直接改。"""
    d = client.post("/projects/mc/intake", json={"opening": "导出"},
                    headers=H("chen")).json()
    r = client.patch(f"/projects/mc/requirements/{d['id']}",
                     json={"title": "导出支持自定义列", "body": "验收：勾选后只导所选列"},
                     headers=H("chen"))
    assert r.status_code == 200
    assert r.json()["title"] == "导出支持自定义列"


def test_confirming_the_draft_starts_the_pipeline(client, world, session):
    d = client.post("/projects/mc/intake", json={"opening": "导出"},
                    headers=H("chen")).json()
    r = client.post(f"/projects/mc/requirements/{d['id']}/submit", headers=H("chen"))
    assert r.status_code == 201
    assert r.json()["state"] == "active" and r.json()["stage"] == "triage"
    kinds = {j.kind for j in session.query(Job).filter(Job.requirement_id == d["id"])}
    assert "advance_requirement" in kinds
    # 确认之后它才上看板
    board = client.get("/projects/mc/requirements", headers=H("chen")).json()
    assert d["id"] in [x["id"] for x in board]


def test_confirming_retires_the_parked_draft_job(client, world, session):
    """草稿阶段挂起的 job 留着，会在人回话时把已进流程的需求拽回草稿态。"""
    d = client.post("/projects/mc/intake", json={"opening": "导出"},
                    headers=H("chen")).json()
    store = JobStore(session)
    job = session.query(Job).filter(Job.requirement_id == d["id"]).one()
    store.park(job)
    session.commit()

    client.post(f"/projects/mc/requirements/{d['id']}/submit", headers=H("chen"))
    session.expire_all()
    assert session.get(Job, job.id).state != "awaiting_signal"


def test_cannot_submit_twice(client, world, session):
    d = client.post("/projects/mc/intake", json={"opening": "导出"},
                    headers=H("chen")).json()
    client.post(f"/projects/mc/requirements/{d['id']}/submit", headers=H("chen"))
    assert client.post(f"/projects/mc/requirements/{d['id']}/submit",
                       headers=H("chen")).status_code == 409


def test_cannot_edit_once_it_is_in_the_pipeline(client, world, session):
    d = client.post("/projects/mc/intake", json={"opening": "导出"},
                    headers=H("chen")).json()
    client.post(f"/projects/mc/requirements/{d['id']}/submit", headers=H("chen"))
    assert client.patch(f"/projects/mc/requirements/{d['id']}",
                        json={"title": "偷改"}, headers=H("chen")).status_code == 409


def test_intake_is_tenant_isolated(client, world, session):
    assert client.post("/projects/mc/intake", json={"opening": "x"},
                       headers=H("sun")).status_code in (403, 404)


# ── 长连接不能占着数据库事务 ────────────────────────────────────
def test_token_last_used_is_not_written_on_every_request(client, world, session):
    """**`api_tokens.last_used_at` 是全表最热的一行。**

    页面每 3 秒轮询一次、SSE 还是长连接，所有请求抢同一行的写锁。
    每次都写的话，SSE 一挂上就把锁攥住，后面的请求排队等到
    `Lock wait timeout exceeded` —— 前端一个红色的 Internal Server Error。
    这个字段只是「最近用过」，粒度到分钟足够。
    """
    from datetime import datetime, timedelta

    from vplatform.api.auth import issue_token
    from vplatform.core.models import ApiToken

    tok = issue_token(session, user_id="chen")
    session.commit()
    row = session.query(ApiToken).filter_by(user_id="chen").one()
    row.last_used_at = datetime.utcnow() - timedelta(seconds=5)
    session.commit()
    before = row.last_used_at

    client.get("/projects", headers={"Authorization": f"Bearer {tok}"})
    session.expire_all()
    assert session.query(ApiToken).filter_by(user_id="chen").one().last_used_at \
        == before, "刚写过又写了一次 —— 这一行会成为锁热点"


def test_token_last_used_is_written_when_stale(client, world, session):
    """太久没更新还是要写 —— 否则这个字段就没意义了。"""
    from datetime import datetime, timedelta

    from vplatform.api.auth import issue_token
    from vplatform.core.models import ApiToken

    tok = issue_token(session, user_id="chen")
    session.commit()
    row = session.query(ApiToken).filter_by(user_id="chen").one()
    row.last_used_at = datetime.utcnow() - timedelta(hours=3)
    session.commit()
    old = row.last_used_at

    client.get("/projects", headers={"Authorization": f"Bearer {tok}"})
    session.expire_all()
    assert session.query(ApiToken).filter_by(user_id="chen").one().last_used_at > old
