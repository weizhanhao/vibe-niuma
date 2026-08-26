"""管理端测试 —— 之前全平台没有任何创建入口，第一个真实用户进不来。"""
import pytest
from fastapi.testclient import TestClient

from vplatform.api.main import app
from vplatform.core.models import ApiToken, Member, Org, Project, ProjectRepo


@pytest.fixture()
def client(engine):
    """**走真实装配路径。**

    不装配 factory 的话 `caps_for` 会回落到全局空 Capabilities，
    流水线永远是默认的 —— 测试和生产走的就不是同一条路了。
    """
    from vplatform.bootstrap import install
    from vplatform.orchestration import handlers

    prev_caps, prev_factory = handlers._caps, handlers._factory
    install()
    try:
        yield TestClient(app)
    finally:
        handlers._caps, handlers._factory = prev_caps, prev_factory


@pytest.fixture()
def org(session):
    o = Org(name="Acme")
    session.add(o)
    session.commit()
    return o


@pytest.fixture()
def admin(session, org, monkeypatch):
    from vplatform.api.auth import issue_token
    monkeypatch.delenv("VP_DEV_AUTH", raising=False)
    raw = issue_token(session, user_id="wei", display_name="管理员")
    session.commit()
    return {"Authorization": f"Bearer {raw}"}


# ── 引导 ─────────────────────────────────────────────────────────
def test_bootstrap_disabled_without_env(client, monkeypatch):
    """**没设引导口令就整个关闭，不是默认放行。**"""
    monkeypatch.delenv("VP_BOOTSTRAP_TOKEN", raising=False)
    r = client.post("/admin/bootstrap", json={"name": "A", "admin_user": "w"})
    assert r.status_code == 403 and "未启用" in r.json()["detail"]


def test_bootstrap_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setenv("VP_BOOTSTRAP_TOKEN", "right")
    assert client.post("/admin/bootstrap", headers={"X-Admin-Token": "wrong"},
                       json={"name": "A", "admin_user": "w"}).status_code == 403


def test_bootstrap_creates_org_and_returns_token_once(client, session, monkeypatch):
    monkeypatch.setenv("VP_BOOTSTRAP_TOKEN", "right")
    r = client.post("/admin/bootstrap", headers={"X-Admin-Token": "right"},
                    json={"name": "Acme", "admin_user": "wei"})
    assert r.status_code == 201
    raw = r.json()["token"]
    assert raw.startswith("vp_")
    session.expire_all()
    row = session.query(ApiToken).one()
    assert row.token_hash != raw            # 只存哈希
    assert session.query(Org).one().name == "Acme"


# ── 空间 ─────────────────────────────────────────────────────────
def test_create_project_makes_creator_admin(client, session, org, admin):
    """建者必须自动成为 admin —— 否则他自己都进不去。"""
    r = client.post("/admin/projects", headers=admin,
                    json={"name": "商户中台", "slug": "mc", "org_id": org.id})
    assert r.status_code == 201
    session.expire_all()
    m = session.query(Member).one()
    assert m.user_id == "wei" and m.role == "admin"


def test_project_secret_is_stored_as_reference_only(client, session, org, admin):
    """**密钥只存引用**。泄库不泄密钥。"""
    client.post("/admin/projects", headers=admin,
                json={"name": "x", "slug": "mc", "org_id": org.id,
                      "llm_secret_ref": "env:DASHSCOPE_API_KEY"})
    session.expire_all()
    p = session.query(Project).one()
    assert p.secret_refs == {"llm": "env:DASHSCOPE_API_KEY"}
    assert "sk-" not in str(p.secret_refs)


@pytest.mark.parametrize("slug", ["MC", "has space", "-lead", "有中文"])
def test_bad_slug_rejected(client, org, admin, slug):
    assert client.post("/admin/projects", headers=admin,
                       json={"name": "x", "slug": slug,
                             "org_id": org.id}).status_code == 422


def test_duplicate_slug_is_409(client, org, admin):
    body = {"name": "x", "slug": "mc", "org_id": org.id}
    assert client.post("/admin/projects", headers=admin, json=body).status_code == 201
    assert client.post("/admin/projects", headers=admin, json=body).status_code == 409


# ── 流水线配置（D8 的 "YAML in DB"）─────────────────────────────
def test_bad_pipeline_rejected_at_config_time(client, org, admin):
    """配错要当场报错，不是等需求跑到那一步才发现 —— 那时已经开了工位烧了 token。"""
    client.post("/admin/projects", headers=admin,
                json={"name": "x", "slug": "mc", "org_id": org.id})
    r = client.put("/admin/projects/mc/pipeline", headers=admin,
                   json={"pipeline": "pipeline:\n  - a: {}\n  - a: {}"})
    assert r.status_code == 422 and "重复" in r.json()["detail"]


def test_pipeline_config_takes_effect_without_restart(client, org, admin):
    """改完流水线立刻生效 —— 不失效缓存的话要重启进程。"""
    client.post("/admin/projects", headers=admin,
                json={"name": "x", "slug": "mc", "org_id": org.id})
    # 跟**默认流水线**比，不写死数字 —— 数字会在加环节时无意义地红一次，
    # 而这个用例要测的是「改了立刻生效」，不是默认有几环
    from vplatform.orchestration.dag import default_pipeline
    assert ([s["key"] for s in
             client.get("/projects/mc/pipeline", headers=admin).json()["stages"]]
            == [s.key for s in default_pipeline().stages])

    client.put("/admin/projects/mc/pipeline", headers=admin, json={"pipeline":
        "pipeline:\n  - clarify: {skill: grilling}\n  - review: {gate: human}"})
    got = [s["key"] for s in client.get("/projects/mc/pipeline",
                                        headers=admin).json()["stages"]]
    assert got == ["clarify", "review"]


# ── 权限 ─────────────────────────────────────────────────────────
def test_non_admin_cannot_change_config(client, session, org, admin):
    from vplatform.api.auth import issue_token

    client.post("/admin/projects", headers=admin,
                json={"name": "x", "slug": "mc", "org_id": org.id})
    p = session.query(Project).one()
    session.add(Member(project_id=p.id, user_id="chen", role="requester"))
    raw = issue_token(session, user_id="chen")
    session.commit()

    H = {"Authorization": f"Bearer {raw}"}
    assert client.post("/admin/projects/mc/repos", headers=H,
                       json={"name": "r", "url": "u"}).status_code == 403
    assert client.put("/admin/projects/mc/pipeline", headers=H,
                      json={"pipeline": "pipeline:\n  - a: {}"}).status_code == 403


def test_non_admin_cannot_issue_token_for_others(client, session, org, admin):
    from vplatform.api.auth import issue_token

    raw = issue_token(session, user_id="chen")
    session.commit()
    H = {"Authorization": f"Bearer {raw}"}
    assert client.post("/admin/tokens", headers=H,
                       json={"user_id": "victim"}).status_code == 403
    # 但可以给自己签
    assert client.post("/admin/tokens", headers=H,
                       json={"user_id": "chen"}).status_code == 201


def test_repo_and_member_crud(client, session, org, admin):
    client.post("/admin/projects", headers=admin,
                json={"name": "x", "slug": "mc", "org_id": org.id})
    r = client.post("/admin/projects/mc/repos", headers=admin,
                    json={"name": "orders-api",
                          "url": "https://github.com/a/orders-api.git"})
    assert r.status_code == 201
    assert client.post("/admin/projects/mc/repos", headers=admin,
                       json={"name": "orders-api", "url": "x"}).status_code == 409
    assert len(client.get("/admin/projects/mc/repos", headers=admin).json()) == 1

    assert client.post("/admin/projects/mc/members", headers=admin,
                       json={"user_id": "chen", "role": "reviewer"}).status_code == 201
    assert client.post("/admin/projects/mc/members", headers=admin,
                       json={"user_id": "chen"}).status_code == 409
    assert client.post("/admin/projects/mc/members", headers=admin,
                       json={"user_id": "x", "role": "god"}).status_code == 422
