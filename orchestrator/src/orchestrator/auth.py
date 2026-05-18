"""Plan 6 Task 2 —— admin token 文件读写 + FastAPI 鉴权依赖。

token 文件：默认 `/opt/vibe-niuma/admin.token`，环境变量 `VIBE_NIUMA_ADMIN_TOKEN_PATH`
可覆盖（测试和本地开发用）。文件不存在则自动生成 `secrets.token_urlsafe(32)` 并
chmod 600；存在则原样读出。

systemd 启动顺序：unit 文件里的 ExecStartPre 兜底也生成一份，但读不到时 Python
这边仍能自己生成 —— 给本地 dev 留路。

用法：
    from orchestrator.auth import verify_admin_token
    app.include_router(admin_router, dependencies=[Depends(verify_admin_token)])
"""
import hmac
import logging
import os
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

logger = logging.getLogger("orchestrator.auth")

# 默认 /opt/vibe-niuma/admin.token；可被 env 覆盖。
# 测试也允许直接 monkeypatch 这个常量。
ADMIN_TOKEN_PATH: str = os.environ.get(
    "VIBE_NIUMA_ADMIN_TOKEN_PATH", "/opt/vibe-niuma/admin.token"
)


def read_admin_token() -> str:
    """读 admin token；不存在则生成 + chmod 600 写入。

    每次都读文件 —— 不缓存，方便用户在 ECS 上手动改 token 后立即生效
    （admin token 不是高频路径，IO 成本可忽略）。
    """
    path = ADMIN_TOKEN_PATH
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()

    # 首次启动：生成 + 写入 + chmod 600
    token = secrets.token_urlsafe(32)
    # 保险起见把父目录建出来（systemd ExecStartPre 已建，本地 dev 不一定）
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # 先创建再 chmod —— 不用 os.umask 是因为 umask 是进程级的，
    # 改了影响其它写文件逻辑；显式 chmod 干净。
    with open(path, "w") as f:
        f.write(token)
    os.chmod(path, 0o600)
    logger.info(f"admin token generated at {path}")
    return token


async def verify_admin_token(
    x_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    """FastAPI dependency：校验 X-Admin-Token 头。

    错/缺都返 401，不区分原因（防止给攻击者多余信息）。
    比对走 `hmac.compare_digest` 防 timing attack。
    """
    if x_admin_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin token",
        )
    expected = read_admin_token()
    if not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin token",
        )
