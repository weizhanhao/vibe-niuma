"""管理端 API。

之前全平台**无法创建 Org / Project / Repo / Member / Token** —— 上线后只能靠
手写 seed 脚本灌库，第一个真实用户没有入口。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from vplatform.api.auth import issue_token
from vplatform.api.deps import current_user, db, project_member
from vplatform.core.models import Member, Org, Project, ProjectRepo

router = APIRouter(prefix="/admin", tags=["admin"])


def _bootstrap_guard(x_admin: str = Header(default="", alias="X-Admin-Token")) -> None:
    """引导期用的一次性口令。

    第一个 Org / Project / token 必须能在没有任何账号的情况下创建 —— 先有鸡还是
    先有蛋。用环境变量里的引导口令守着，**没设就整个关闭引导接口**，
    而不是默认放行。
    """
    import os
    expected = os.environ.get("VP_BOOTSTRAP_TOKEN", "")
    if not expected:
        raise HTTPException(403, "引导接口未启用（需设置 VP_BOOTSTRAP_TOKEN）")
    import hmac
    if not hmac.compare_digest(x_admin, expected):
        raise HTTPException(403, "引导口令不正确")


def _require_admin(pm=Depends(project_member)) -> tuple[Project, Member]:
    p, m = pm
    if m.role != "admin":
        raise HTTPException(403, "只有 admin 能改空间配置")
    return p, m


# ── 引导 ─────────────────────────────────────────────────────────
class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    admin_user: str = Field(min_length=1, max_length=120)


class TokenOut(BaseModel):
    token: str
    user_id: str
    note: str = "明文只返回这一次，请立刻保存"


@router.post("/bootstrap", response_model=TokenOut, status_code=201,
             dependencies=[Depends(_bootstrap_guard)])
def bootstrap(payload: OrgIn, s: Session = Depends(db)):
    """建第一个组织 + 第一个管理员 token。"""
    org = Org(name=payload.name)
    s.add(org)
    s.flush()
    raw = issue_token(s, user_id=payload.admin_user, display_name="bootstrap admin")
    return TokenOut(token=raw, user_id=payload.admin_user)


# ── 空间 ─────────────────────────────────────────────────────────
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    org_id: str
    target_branch: str = "vibe/dev"
    dev_model: str = "deepseek-v4-pro"
    workspaces_root: str = "/data/projects"
    llm_secret_ref: str = "env:DASHSCOPE_API_KEY"
    quota_parallel_runs: int = Field(default=8, ge=1, le=64)


@router.post("/projects", status_code=201)
def create_project(payload: ProjectIn, s: Session = Depends(db),
                   user: str = Depends(current_user)):
    if s.execute(select(Project).where(Project.slug == payload.slug)).scalar_one_or_none():
        raise HTTPException(409, f"slug {payload.slug} 已被占用")
    if s.get(Org, payload.org_id) is None:
        raise HTTPException(404, "组织不存在")

    p = Project(org_id=payload.org_id, name=payload.name, slug=payload.slug,
                target_branch=payload.target_branch, dev_model=payload.dev_model,
                workspaces_root=payload.workspaces_root,
                quota_parallel_runs=payload.quota_parallel_runs,
                # **密钥只存引用**，不存明文
                secret_refs={"llm": payload.llm_secret_ref})
    s.add(p)
    s.flush()
    # 建者自动成为 admin，否则他自己都进不去
    s.add(Member(project_id=p.id, user_id=user, role="admin"))
    s.flush()
    return {"id": p.id, "slug": p.slug, "name": p.name}


class PipelineIn(BaseModel):
    pipeline: str = Field(min_length=1)


@router.put("/projects/{slug}/pipeline")
def set_pipeline(payload: PipelineIn, pm=Depends(_require_admin),
                 s: Session = Depends(db)):
    """把流水线 YAML 存进空间配置（D8 的 "YAML in DB"）。

    **加载期就校验**，配错了当场报错，不是等跑到那一步才发现。
    """
    from vplatform.orchestration.dag import PipelineError, load_pipeline

    p, _ = pm
    try:
        pipe = load_pipeline(payload.pipeline)
    except PipelineError as exc:
        raise HTTPException(422, f"流水线配置非法：{exc}") from exc

    p.config = {**(p.config or {}), "pipeline": payload.pipeline}
    p.version += 1
    s.flush()

    from vplatform.bootstrap import get_factory
    get_factory().invalidate(p.id)      # 不失效的话要重启进程才生效
    return {"stages": [st.key for st in pipe.stages],
            "required_skills": sorted(pipe.required_skills)}


class DeployIn(BaseModel):
    env_config: dict


@router.put("/projects/{slug}/deploy")
def set_deploy(payload: DeployIn, pm=Depends(_require_admin), s: Session = Depends(db)):
    """配各环境的部署命令。之前 SelfHostedDeploy 的 env_config 没人填，
    必然抛「环境 X 没有配置部署命令」。"""
    p, _ = pm
    p.config = {**(p.config or {}), "deploy": payload.env_config}
    p.version += 1
    s.flush()
    from vplatform.bootstrap import get_factory
    get_factory().invalidate(p.id)
    return {"envs": sorted(payload.env_config)}


# ── 仓 ───────────────────────────────────────────────────────────
class RepoIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=512)
    host_kind: str = "github"
    default_branch: str = "main"
    pat_ref: str | None = None


@router.post("/projects/{slug}/repos", status_code=201)
def add_repo(payload: RepoIn, pm=Depends(_require_admin), s: Session = Depends(db)):
    p, _ = pm
    if s.execute(select(ProjectRepo).where(ProjectRepo.project_id == p.id,
                                           ProjectRepo.name == payload.name)
                 ).scalar_one_or_none():
        raise HTTPException(409, f"仓 {payload.name} 已存在")
    r = ProjectRepo(project_id=p.id, name=payload.name, url=payload.url,
                    host_kind=payload.host_kind,
                    default_branch=payload.default_branch, pat_ref=payload.pat_ref)
    s.add(r)
    s.flush()
    return {"id": r.id, "name": r.name}


@router.get("/projects/{slug}/repos")
def list_repos(pm=Depends(project_member), s: Session = Depends(db)):
    p, _ = pm
    rows = s.execute(select(ProjectRepo).where(ProjectRepo.project_id == p.id)).scalars()
    return [{"id": r.id, "name": r.name, "url": r.url, "host_kind": r.host_kind,
             "default_branch": r.default_branch} for r in rows]


# ── 成员 / token ─────────────────────────────────────────────────
class MemberIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=120)
    display_name: str = ""
    role: str = Field(default="requester", pattern="^(requester|reviewer|admin)$")


@router.post("/projects/{slug}/members", status_code=201)
def add_member(payload: MemberIn, pm=Depends(_require_admin), s: Session = Depends(db)):
    p, _ = pm
    if s.execute(select(Member).where(Member.project_id == p.id,
                                      Member.user_id == payload.user_id)
                 ).scalar_one_or_none():
        raise HTTPException(409, "该成员已存在")
    m = Member(project_id=p.id, user_id=payload.user_id,
               display_name=payload.display_name, role=payload.role)
    s.add(m)
    s.flush()
    return {"id": m.id, "user_id": m.user_id, "role": m.role}


class IssueTokenIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=120)
    display_name: str = ""


@router.post("/tokens", response_model=TokenOut, status_code=201)
def create_token(payload: IssueTokenIn, s: Session = Depends(db),
                 user: str = Depends(current_user)):
    """签发 token。之前 issue_token 没有任何路由暴露 ——
    生产模式下签不出 token 就等于无法登录。"""
    is_admin = s.execute(
        select(Member).where(Member.user_id == user, Member.role == "admin")
    ).scalars().first()
    if is_admin is None and payload.user_id != user:
        raise HTTPException(403, "只有 admin 能给别人签 token")
    raw = issue_token(s, user_id=payload.user_id, display_name=payload.display_name)
    return TokenOut(token=raw, user_id=payload.user_id)
