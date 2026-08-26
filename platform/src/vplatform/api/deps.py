"""API 依赖 —— 租户隔离在这里强制。"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from vplatform.core.db import session_scope
from vplatform.core.models import Member, Project


def db() -> Iterator[Session]:
    with session_scope() as s:
        yield s


def current_user(
    s: Session = Depends(db),
    authorization: str = Header(default="", alias="Authorization"),
    x_user: str = Header(default="", alias="X-User"),
    token: str = Query(default=""),
    devUser: str = Query(default=""),   # noqa: N803 —— 与前端 query 名一致
) -> str:
    """解析调用者身份。

    生产：`Authorization: Bearer vp_xxx`（token 哈希入库，明文只发一次）。
    开发：`X-User: chen` —— **仅在 VP_DEV_AUTH=1 时生效**，
    否则任何人加个头就能冒充任意用户。
    """
    from vplatform.api.auth import dev_mode, resolve_principal

    # EventSource 不支持自定义头，SSE 端点只能走 query 传凭证。
    bearer = (authorization[7:].strip()
              if authorization.lower().startswith("bearer ") else None) or token or None
    user = resolve_principal(s, bearer=bearer, x_user=x_user or devUser)
    if not user:
        hint = "需要 Authorization: Bearer <token>"
        if x_user and not dev_mode():
            hint += "（检测到 X-User，但它只在 VP_DEV_AUTH=1 的开发模式下生效）"
        raise HTTPException(401, hint)
    return user


def project_member(
    slug: str = Path(...),
    s: Session = Depends(db),
    user: str = Depends(current_user),
) -> tuple[Project, Member]:
    """**所有涉及空间的路由都必须过这里。**

    租户隔离不能靠「记得加 where project_id」—— 那种约定迟早会被漏掉。
    """
    p = s.execute(select(Project).where(Project.slug == slug)).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, f"空间 {slug} 不存在")
    m = s.execute(
        select(Member).where(Member.project_id == p.id, Member.user_id == user)
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(403, f"{user} 不是空间 {slug} 的成员")
    return p, m


def require_reviewer(pm: tuple[Project, Member] = Depends(project_member)) -> tuple[Project, Member]:
    _, m = pm
    if m.role not in ("reviewer", "admin"):
        raise HTTPException(403, "只有 reviewer / admin 能审核")
    return pm
