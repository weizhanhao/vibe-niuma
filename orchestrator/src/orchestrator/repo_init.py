"""RepoInitializer —— 扫一遍 demo 仓库、生成项目概览 AGENTS.md。

仓库首次接入或缺 AGENTS.md 时跑一次。等 dev runner CLI (opencode / claude)
自主读源码、写文档、退出；本类只负责调度和状态追踪。

集成点：
- main.py lifespan 启动时创建实例 + 异步 ensure()
- BrainstormingSkill.clarify() 第一步 await wait_ready() 并 load doc_content()
- REST: GET /repo/status, POST /repo/init (force=true)

设计取舍：
- 不写到 DB —— 仓库本身就是真相，AGENTS.md 走 git 跟踪可手编可 diff
- 不阻塞 orchestrator 启动 —— 第一条 CR 才阻塞等就绪
- 失败可重试 —— /repo/init 可强制重跑；status=failed 时下次启动还会再试
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Final


# 状态常量。string 而非 Enum 是为了 REST 端直接序列化。
class RepoInitStatus:
    NOT_INITIALIZED: Final = "not-initialized"
    INITIALIZING:    Final = "initializing"
    READY:           Final = "ready"
    FAILED:          Final = "failed"


# 喂给 dev runner CLI 的 prompt。中文 + 业务语言为主，要求 LLM 直接写文件落地。
INIT_PROMPT: Final = """\
你正在为这个 web 项目生成一份「项目概览」文档，给后续 AI 改代码当上下文用。

任务：扫描仓库当前代码（不要假设、不要瞎编），生成 `AGENTS.md` 写到仓库**根目录**。
结构如下：

# 项目概览

## 技术栈
列出前端框架/版本、后端框架/版本、数据库、关键依赖。

## 路由表 (URL → 源文件)
| URL | 组件/路由文件 | 用途 |
|---|---|---|
扫前端路由配置 + 后端 router 列文件清单。

## 关键目录
- frontend/src/pages/    ← 各页面（列出几个代表）
- frontend/src/components/  ← 共享组件
- frontend/src/api/    ← 后端 API 调用
- backend/.../routers/    ← API 路由
- backend/.../models.py   ← 数据模型

## 数据模型
按表/实体列出关键字段。例：
- Order (id, customer_name, status, total_amount, created_at)

## 业务约定
- 订单状态枚举、显示规则、币种格式、命名习惯等
- 业务员关心、但代码里不一定明显的事

## 构建/测试命令
- 前端构建：`npm run build` (cwd: frontend/)
- 后端启动：`uvicorn ... --port 8000`
- 测试：`pytest`、`vitest` 等具体命令

要求：
- 中文，业务语言优先，少用技术黑话
- 内容**直接来自你读到的代码**，不要瞎猜
- 整个文档 200-400 行，简洁可扫
- 把文档**直接写到仓库根目录的 `AGENTS.md`**（覆盖已有）
- 完成后不需要 commit，调用方会处理
"""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """跑一条 `git -C <repo> ...`，silent，不抛。"""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class RepoInitializer:
    """单例：管 AGENTS.md 的生成 + 状态。"""

    def __init__(
        self,
        repo_path: Path | str,
        *,
        dev_runner: str = "opencode",
        dev_model: str = "deepseek/deepseek-v4-flash",
        timeout_seconds: int = 600,
        doc_filename: str = "AGENTS.md",  # agents.md 跨厂商开放约定
    ):
        self.repo_path = Path(repo_path)
        self.dev_runner = dev_runner
        self.dev_model = dev_model
        self.timeout_seconds = timeout_seconds
        self.doc_path = self.repo_path / doc_filename

        self._status = RepoInitStatus.NOT_INITIALIZED
        self._error: str | None = None
        self._completed_at: datetime | None = None
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()  # 防止并发 init

    # ── 公开只读状态 ───────────────────────────────────────────────
    @property
    def status(self) -> str:
        return self._status

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def completed_at(self) -> datetime | None:
        return self._completed_at

    @property
    def is_ready(self) -> bool:
        return self._status == RepoInitStatus.READY

    async def wait_ready(self, timeout: float | None = None) -> bool:
        """阻塞直到 init 完成或超时；超时返回 False。"""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def doc_content(self) -> str:
        """返回 AGENTS.md 内容；不存在时空串。BrainstormingSkill 直接调这个。"""
        if self.doc_path.exists():
            return self.doc_path.read_text(encoding="utf-8", errors="replace")
        return ""

    # ── 主流程 ─────────────────────────────────────────────────────
    async def ensure(self, force: bool = False) -> None:
        """文档存在且 !force → 标 ready 返回；否则跑 init。"""
        async with self._lock:
            if not force and self.doc_path.exists():
                self._mark_ready_from_file()
                return
            try:
                await self._run_init()
            except Exception as e:
                self._status = RepoInitStatus.FAILED
                self._error = str(e)[:500]
                # 失败时不 set _ready —— callers 仍然能 retry/超时
                raise

    def _mark_ready_from_file(self) -> None:
        mtime = self.doc_path.stat().st_mtime
        self._completed_at = datetime.utcfromtimestamp(mtime)
        self._status = RepoInitStatus.READY
        self._error = None
        self._ready.set()

    async def _run_init(self) -> None:
        self._status = RepoInitStatus.INITIALIZING
        self._ready.clear()

        argv, env = self._build_argv()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"/init 超时（>{self.timeout_seconds}s）；尝试用更轻的模型或缩短 prompt"
            )

        if proc.returncode != 0:
            tail = stderr_b.decode("utf-8", errors="replace")[-1500:]
            raise RuntimeError(f"/init CLI 非 0 退出 (rc={proc.returncode})\n{tail}")

        if not self.doc_path.exists():
            raise RuntimeError(
                "/init 跑完但 AGENTS.md 没生成 —— dev runner 没按 prompt 写文件"
            )

        # 落地后顺手 commit。失败不致命（仓库可能不是 git）。
        await asyncio.to_thread(self._commit_doc)

        self._completed_at = datetime.utcnow()
        self._status = RepoInitStatus.READY
        self._error = None
        self._ready.set()

    def _build_argv(self) -> tuple[list[str], dict]:
        """根据 dev_runner 选择正确的 argv + env。"""
        env = {**os.environ}
        if self.dev_runner == "opencode":
            argv = ["opencode", "run", INIT_PROMPT, "--model", self.dev_model]
            # opencode 直连 provider 用 os.environ 里已有的真 key
        else:  # claude-code 或回退
            argv = ["claude", "-p", INIT_PROMPT, "--model", self.dev_model]
            env.setdefault("ANTHROPIC_BASE_URL", os.environ.get(
                "ANTHROPIC_BASE_URL", "http://127.0.0.1:8787"
            ))
            env.setdefault("ANTHROPIC_API_KEY", os.environ.get(
                "ANTHROPIC_API_KEY", os.environ.get("LLM_API_KEY", "")
            ))
        return argv, env

    def _commit_doc(self) -> None:
        # 配 safe.directory 避免 alien-ownership 报错
        _git(self.repo_path, "config", "--global", "--add",
             "safe.directory", str(self.repo_path))
        _git(self.repo_path, "config", "user.email", "doskill-init@local")
        _git(self.repo_path, "config", "user.name", "doskill /init")
        _git(self.repo_path, "add", self.doc_path.name)
        _git(self.repo_path, "commit", "-q", "-m",
             "chore: doskill /init - regenerate AGENTS.md")
