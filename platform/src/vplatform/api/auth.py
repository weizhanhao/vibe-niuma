"""认证。

**`X-User` 头只是开发模式的便捷入口，不能上生产。**
生产走 bearer token：token 哈希入库，明文只在创建时返回一次。

将来接企业 IdP（OIDC）是在 `resolve_principal` 里加一条分支，
不改任何路由 —— 同 D10 的接缝纪律。
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

# ApiToken 定义在 core.models —— 模型全部挂在同一个 Base 上，
# 否则 create_all 可能漏建表（见 models.py 里的说明）
from vplatform.core.models import ApiToken


# `last_used_at` 的写入粒度。它只是「最近用过」，不需要精确到秒，
# 而每秒写一次会让这一行成为全表最热的锁点。
_LAST_USED_GRANULARITY = timedelta(minutes=5)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(session: Session, *, user_id: str, display_name: str = "") -> str:
    """签发 token。**明文只在这里返回一次**，之后无法找回。"""
    raw = f"vp_{secrets.token_urlsafe(32)}"
    session.add(ApiToken(user_id=user_id, display_name=display_name,
                         token_hash=_hash(raw)))
    session.flush()
    return raw


def resolve_principal(session: Session, *, bearer: str | None,
                      x_user: str | None) -> str | None:
    """把凭证解析成 user_id。解析不出返回 None，由路由层决定怎么拒。"""
    if bearer:
        tok = session.execute(
            select(ApiToken).where(ApiToken.token_hash == _hash(bearer))
        ).scalar_one_or_none()
        if tok is not None:
            # **不要每个请求都写这一行。**
            # 它是全表最热的一行：页面每 3 秒轮询一次、SSE 还是长连接，
            # 所有请求抢同一行的写锁。而 SSE 在鉴权时拿了锁之后**整条连接
            # 期间不放**（几分钟），后面每个请求都排队等到
            # `Lock wait timeout exceeded` —— 前端就是一个红色的
            # Internal Server Error。实测用户反复撞到。
            #
            # 这个字段只是「最近用过」，精确到分钟足够了。
            now = datetime.utcnow()
            if (tok.last_used_at is None
                    or (now - tok.last_used_at) > _LAST_USED_GRANULARITY):
                tok.last_used_at = now
            return tok.user_id
        return None

    # 开发模式：X-User 直接当身份。**必须显式开启**，默认关闭 —— 否则
    # 生产上任何人加个头就能冒充任意用户。
    if x_user and dev_mode():
        return x_user
    return None


def dev_mode() -> bool:
    return os.environ.get("VP_DEV_AUTH", "").lower() in ("1", "true", "yes")
