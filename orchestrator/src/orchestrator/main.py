"""FastAPI app —— REST + SSE 端点、依赖装配、lifespan。

Plan 2 用 fake adapter 装配（见 AppState.build_pipeline）；Plan 3 在 build_pipeline
里换成真实 adapter —— 那是 Plan 3 唯一的接线改动点。
"""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

logger = logging.getLogger("orchestrator.main")

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from pathlib import Path

from pydantic import BaseModel, Field

from orchestrator.admin import router as admin_router
from orchestrator.adapters.fakes import FakePreviewAdapter  # 仅 reaper 用（fallback）
from orchestrator.adapters.interfaces import PreviewAdapter
from orchestrator.adapters.impl.brainstorming_skill import BrainstormingSkill
from orchestrator.adapters.impl.claude_code_runner import ClaudeCodeDevRunner
from orchestrator.adapters.impl.docker_preview import DockerPreviewAdapter
from orchestrator.adapters.impl._llm import LLMClient
from orchestrator.adapters.impl.opencode_runner import OpenCodeDevRunner
from orchestrator.adapters.impl.react_vite_stack import ReactViteStackAdapter
from orchestrator.adapters.types import RawRequest
from orchestrator.config import settings
from orchestrator.conversation import ConversationRepository
from orchestrator.db import Base, engine, get_db
from orchestrator.events import Event, EventBus
from orchestrator.git_manager import GitConflictError, GitManager
from orchestrator.multi_repo_sync import RepoSpec, sync_repos as run_sync_repos
from orchestrator.pipeline import Pipeline
from orchestrator.quota import QuotaManager
from orchestrator.repo_init import RepoInitializer, RepoInitStatus
from orchestrator.repository import ChangeRequestRepository
from orchestrator.schemas import (
    AnswerIn, ChangeRequestOut, CreateChangeRequestIn,
    PostMessageIn, PostMessageOut, RuntimeErrorsIn,
)
from orchestrator.states import TERMINAL, State


class AppState:
    """进程内单例：事件总线、配额、Pipeline、后台任务集、可注入 session factory。"""

    def __init__(self) -> None:
        # Plan 11 M3.T18：/health 算 uptime_seconds 用
        self.start_time_monotonic: float = time.monotonic()
        # Plan 11 M3.T18：最近一次后台失败的简述（供 /health.last_error）
        self.last_error: Optional[str] = None
        self.event_bus = EventBus()
        self.quota = QuotaManager(capacity=settings.quota_size)
        # 每个变更请求一条 Pipeline —— 单槽会让并发请求互相覆盖、串掉 /answer 路由
        self.pipelines: dict[str, Pipeline] = {}
        self.tasks: set[asyncio.Task] = set()
        self.session_factory = None
        # pipeline_factory 默认是 _build_real_pipeline；测试可注入 fake 实现
        self.pipeline_factory = self._build_real_pipeline
        # 项目级 /init —— 由 lifespan 装填；测试场景留 None
        self.repo_initializer: RepoInitializer | None = None
        # 共用预览适配器：reaper / merge / discard / failed 都从这里拆容器，
        # 单例保证 used_ports 一致，避免容器泄漏。lifespan 装填。
        self.preview: PreviewAdapter | None = None
        # Plan 10 Task 10：POST /messages 路由用的两个依赖。lifespan 装填真实实现，
        # 测试场景 conftest 注入 fake。
        self.intent_classifier = None
        # chat_responder_factory(db) → ChatResponder 绑定那个 session 的 conv repo。
        # 用 factory 是因为 ChatResponder 持有 ConversationRepository(db) 引用，
        # 必须每请求一只新的。
        self.chat_responder_factory = None

    def build_pipeline(self, db: Session) -> Pipeline:
        return self.pipeline_factory(db)

    def _build_real_pipeline(self, db: Session) -> Pipeline:
        """Plan 3：装配真实 adapter；Plan 5 在 ECS 上跑的就是这套。"""
        if settings.dev_runner == "opencode":
            dev_runner = OpenCodeDevRunner()
        else:
            dev_runner = ClaudeCodeDevRunner()
        # 共用一个 stack_adapter：pipeline 用来 locate+context_pack，
        # BrainstormingSkill 用来在 brainstorm 时 grep 当前 URL 对应组件的真 UI 标签。
        stack_adapter = ReactViteStackAdapter(repo_path=settings.demo_repo_path)
        return Pipeline(
            repo_path=settings.demo_repo_path,
            repository=ChangeRequestRepository(db),
            git_manager=GitManager(settings.demo_repo_path),
            event_bus=self.event_bus,
            quota=self.quota,
            interaction_skill=BrainstormingSkill(
                LLMClient(),
                repo_initializer=self.repo_initializer,
                stack_adapter=stack_adapter,
                repo_path=settings.demo_repo_path,
            ),
            stack_adapter=stack_adapter,
            dev_runner=dev_runner,
            # lifespan 装填的单例；测试场景 (preview=None) 回退到新建一个，
            # 保证测试不依赖 lifespan 顺序。
            preview_adapter=self.preview or DockerPreviewAdapter(
                port_min=settings.preview_port_min,
                port_max=settings.preview_port_max,
                preview_host=settings.preview_host,
                docker_network=settings.docker_network,
                backend_url=settings.preview_backend_url,
            ),
        )


app_state = AppState()


def _ensure_schema_migrations(engine_) -> None:
    """Plan 8/9 加列的 idempotent migration —— ECS 上没跑 alembic，靠 lifespan 自适应。

    Base.metadata.create_all 只建新表（conversation / system_config），既有的
    change_requests 表多出来的列（Plan 8 加 repos JSON、Plan 9 加 conversation_id
    VARCHAR(32)）它不会补。这里查 information_schema 看列是否存在，缺就 ALTER。
    幂等：每次启动跑都安全。失败（无权限 / 不是 MySQL）只 warn 不阻塞。
    """
    import logging as _logging
    from sqlalchemy import text
    _log = _logging.getLogger(__name__)
    # SQLite（测试）跳过 —— 测试用 conftest 重建表，不存在「老 schema」问题
    if engine_.dialect.name != "mysql":
        return
    desired_cols: list[tuple[str, str, str]] = [
        # Plan 8/9 列
        ("change_requests", "repos", "JSON NULL"),
        ("change_requests", "conversation_id", "VARCHAR(32) NULL"),
        # Plan 10 列
        ("change_requests", "attachments", "JSON NULL"),
        ("change_requests", "mode", "VARCHAR(16) NULL"),
        ("change_requests", "refine_of", "VARCHAR(36) NULL"),
        ("change_requests", "self_heal_attempts", "INT NOT NULL DEFAULT 0"),
    ]
    desired_indexes: list[tuple[str, str, str]] = [
        ("change_requests", "idx_change_requests_conversation_id", "conversation_id"),
    ]
    try:
        with engine_.begin() as conn:
            for table, col, ddl in desired_cols:
                exists = conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
                ), {"t": table, "c": col}).scalar()
                if not exists:
                    _log.warning("schema migration: ALTER TABLE %s ADD COLUMN %s %s", table, col, ddl)
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            for table, idx, cols in desired_indexes:
                exists = conn.execute(text(
                    "SELECT 1 FROM information_schema.statistics "
                    "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
                ), {"t": table, "i": idx}).scalar()
                if not exists:
                    _log.warning("schema migration: CREATE INDEX %s ON %s(%s)", idx, table, cols)
                    conn.execute(text(f"ALTER TABLE {table} ADD INDEX {idx} ({cols})"))
    except Exception as exc:  # noqa: BLE001
        _log.warning("schema migration 失败（不阻塞启动）：%s", exc)


def _bucket_legacy_crs(db: Session) -> None:
    """Plan 9 Task 6：现存 conversation_id IS NULL 的 CR → bucket 到 Legacy。
    幂等：已有 Legacy + 已 bucket 完的 CR 跳过。"""
    from sqlalchemy import select, update as sa_update
    from orchestrator.models import ChangeRequest, Conversation

    # CR 数量 = 0 时跳过
    null_count = db.scalar(
        select(ChangeRequest).where(ChangeRequest.conversation_id.is_(None)).limit(1)
    )
    if null_count is None:
        return
    # 查或建 Legacy（按 title 唯一性约定）
    legacy = db.scalar(select(Conversation).where(Conversation.title == "Legacy（迁移自 v0.4）"))
    if legacy is None:
        legacy = Conversation(
            id="legacy" + "0" * 26,
            title="Legacy（迁移自 v0.4）",
            messages=[],
        )
        db.add(legacy)
        db.commit()
    # 批量 update
    db.execute(
        sa_update(ChangeRequest)
        .where(ChangeRequest.conversation_id.is_(None))
        .values(conversation_id=legacy.id)
    )
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    _ensure_schema_migrations(engine)
    from orchestrator.db import SessionLocal
    from orchestrator.reaper import run_reaper_loop

    session_factory = app_state.session_factory or SessionLocal

    # 重启恢复：把残留的非终态请求标 failed(interrupted)
    db = session_factory()
    try:
        repo = ChangeRequestRepository(db)
        for cr in repo.list_non_terminal():
            repo.mark_failed(
                cr.id, phase="interrupted", reason="orchestrator-restart", log=""
            )
        # Plan 9 Task 6：bucket 所有 conversation_id IS NULL 的 CR 到一个 Legacy
        # conversation（每个 orchestrator 实例一个 bucket，只在第一次运行时创建）
        _bucket_legacy_crs(db)
    finally:
        db.close()

    # 项目级 /init：异步触发，缺 AGENTS.md 就跑；存在则立刻 ready
    app_state.repo_initializer = RepoInitializer(
        repo_path=settings.demo_repo_path,
        dev_runner=settings.dev_runner,
        dev_model=settings.dev_model,
        timeout_seconds=settings.repo_init_timeout_seconds,
        doc_filename=settings.repo_init_doc_filename,
    )
    init_task = asyncio.create_task(app_state.repo_initializer.ensure(force=False))
    app_state.tasks.add(init_task)
    init_task.add_done_callback(app_state.tasks.discard)

    # 启动闲置回收后台循环；reaper / pipeline / merge-teardown 共用一个 adapter，
    # 单例保证内存里的 _used_ports 不被多个实例分裂，避免端口分配冲突。
    app_state.preview = DockerPreviewAdapter(
        port_min=settings.preview_port_min,
        port_max=settings.preview_port_max,
        preview_host=settings.preview_host,
        docker_network=settings.docker_network,
        backend_url=settings.preview_backend_url,
    )
    reaper_task = asyncio.create_task(
        run_reaper_loop(
            session_factory=session_factory,
            quota=app_state.quota,
            preview_adapter=app_state.preview,
            ttl_seconds=settings.idle_ttl_seconds,
            interval_seconds=settings.reaper_interval_seconds,
        )
    )

    # Plan 10 Task 10：装配 /messages 端点的两个依赖。共用一只 LLMClient 即可
    # （内部 httpx 是请求级 client，跨调用 safe）。chat_responder 必须每请求一只
    # 新的（绑定 session 的 ConversationRepository），所以暴露 factory。
    from orchestrator.intent_classifier import IntentClassifier
    from orchestrator.chat_responder import ChatResponder
    shared_llm = LLMClient()
    app_state.intent_classifier = IntentClassifier(shared_llm)

    def _make_chat_responder(db: Session) -> ChatResponder:
        return ChatResponder(shared_llm, ConversationRepository(db))
    app_state.chat_responder_factory = _make_chat_responder

    yield
    reaper_task.cancel()
    try:
        await reaper_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="AI 原生低代码平台 — Orchestrator", lifespan=lifespan)
# Plan 6 Task 3：/admin/config GET/PUT —— 扩展端配置面板的 REST 通道
app.include_router(admin_router)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    app_state.tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        app_state.tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            # 不让后台 task 的异常静默吞掉 —— pipeline.run 自己已经会 mark_failed，
            # 这里兜底捕获更上层的逻辑错误（任务还没进 try 就崩、event loop 异常等），
            # 让 journalctl 能看到 traceback。
            logger.exception("background task crashed", exc_info=exc)

    task.add_done_callback(_on_done)


@app.get("/health")
async def health() -> dict:
    """Plan 11 M3.T18：业务员 UI 用的健康指示灯 + 报告给程序员按钮。

    services 探测 timeout=2s/service，总时长 ~4s 内（mysql + 两个 HTTP）。
    """
    from orchestrator.health import build_health_payload

    payload = await build_health_payload(
        start_time_monotonic=app_state.start_time_monotonic,
        session_factory=app_state.session_factory,
        llm_proxy_url=getattr(settings, "llm_proxy_health_url", ""),
        main_demo_url=getattr(settings, "main_demo_health_url", ""),
        last_error=app_state.last_error,
    )
    return {
        "status": payload.status,
        "services": payload.services,
        "uptime_seconds": payload.uptime_seconds,
        "last_cr_at": payload.last_cr_at,
        "last_error": payload.last_error,
        "version": payload.version,
    }


@app.get("/repo/status")
def repo_status() -> dict:
    """/init 状态：扩展用来显示「正在理解项目…」横幅。"""
    init = app_state.repo_initializer
    if init is None:
        return {
            "status": RepoInitStatus.NOT_INITIALIZED,
            "error": None,
            "completed_at": None,
            "doc_path": None,
            "doc_exists": False,
        }
    return {
        "status": init.status,
        "error": init.error,
        "completed_at": init.completed_at.isoformat() if init.completed_at else None,
        "doc_path": str(init.doc_path),
        "doc_exists": init.doc_path.exists(),
    }


@app.post("/repo/init")
async def repo_init_force() -> dict[str, str]:
    """强制重 init（手编了 AGENTS.md 想让 AI 重写时调）。立返 initializing。"""
    init = app_state.repo_initializer
    if init is None:
        raise HTTPException(status_code=503, detail="repo initializer 未就绪")
    _spawn(init.ensure(force=True))
    return {"status": RepoInitStatus.INITIALIZING}


@app.post("/change-requests", response_model=ChangeRequestOut)
async def create_change_request(
    payload: CreateChangeRequestIn, db: Session = Depends(get_db)
) -> ChangeRequestOut:
    repo = ChangeRequestRepository(db)
    # Plan 10 Task 9：normalize_attachments 把老 single screenshot_b64 / 新
    # attachments[] 统一成 dict list，dataclass RawRequest 直接吃。
    normalized = payload.normalize_attachments()
    raw = RawRequest(
        url=payload.url,
        screenshot_b64=payload.screenshot_b64,
        box_coords=payload.box_coords,
        viewport=payload.viewport,
        request_text=payload.request_text,
        attachments=[a.model_dump() for a in normalized] if normalized else [],
    )
    # Plan 9 Task 3：conversation_id 缺省时自动 create 一个新对话
    conv_repo = ConversationRepository(db)
    conv_id = payload.conversation_id
    if conv_id is None:
        conv = conv_repo.create()
        conv_id = conv.id
    elif conv_repo.get(conv_id) is None:
        raise HTTPException(status_code=404, detail=f"conversation {conv_id} 不存在")
    cr = repo.create(raw, conversation_id=conv_id)
    # 把用户消息 append 到 conversation
    conv_repo.append_message(conv_id, {
        "type": "user",
        "ts": datetime.utcnow().isoformat(),
        "content": payload.request_text,
        "cr_id": cr.id,
    })
    pipeline = app_state.build_pipeline(db)
    app_state.pipelines[cr.id] = pipeline
    _spawn(pipeline.run(cr.id))
    return ChangeRequestOut.from_model(cr)


@app.get("/change-requests/{request_id}", response_model=ChangeRequestOut)
def get_change_request(
    request_id: str, db: Session = Depends(get_db)
) -> ChangeRequestOut:
    cr = ChangeRequestRepository(db).get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="变更请求不存在")
    return ChangeRequestOut.from_model(cr)


@app.post("/conversations/{conv_id}/messages", response_model=PostMessageOut)
async def post_message(
    conv_id: str, payload: PostMessageIn, db: Session = Depends(get_db)
) -> PostMessageOut:
    """Plan 10 Task 10：统一 message 入口。

    业务员发的每条 message（chat / 截图改 / refine）都进这里：
    1. 先 append user message 到 conversation（含 attachments）—— 永不丢历史
    2. intent_classifier 判路由（new_cr / refine_cr / chat_only）
    3. dispatch：起 pipeline 或调 ChatResponder
    4. 返 message_id + mode + (cr_id | ai_message_id) + classifier 信心
    """
    conv_repo = ConversationRepository(db)
    conv = conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail=f"conversation {conv_id} 不存在")

    cr_repo = ChangeRequestRepository(db)

    # 1) 把 user message 落库（永远先记历史，即使 dispatch 失败 conv 也有完整记录）
    import uuid as _uuid
    user_msg_id = _uuid.uuid4().hex
    attachments_dump = (
        [a.model_dump() for a in payload.attachments]
        if payload.attachments else None
    )
    user_msg: dict = {
        "id": user_msg_id,
        "type": "user",
        "ts": datetime.utcnow().isoformat(),
        "content": payload.text,
    }
    if attachments_dump:
        user_msg["attachments"] = attachments_dump
    conv_repo.append_message(conv_id, user_msg)

    # 2) 找 last CR 状态喂 classifier
    last_cr = cr_repo.latest_in_conversation(conv_id)
    last_cr_state = last_cr.state if last_cr else None

    # 3) classify intent（fake 测试或缺失也能跑：缺 classifier → 兜底 new_cr 走老路径）
    classifier = app_state.intent_classifier
    if classifier is None:
        # 没装 classifier（旧 lifespan 没就绪）→ 走 new_cr 保守路径
        from orchestrator.intent_classifier import IntentDecision
        decision = IntentDecision(mode="new_cr", confidence=0.5, reason="classifier 未装配")
    else:
        # 拉 conversation 历史给 classifier（messages 最近 6 条由 classifier 自己截）
        history = list(conv.messages or [])
        repo_doc = ""
        if app_state.repo_initializer is not None:
            try:
                repo_doc = app_state.repo_initializer.doc_content() or ""
            except Exception:  # noqa: BLE001
                repo_doc = ""
        decision = await classifier.classify(
            message_text=payload.text,
            conversation_messages=history,
            last_cr_state=last_cr_state,
            repo_doc=repo_doc,
            override=payload.override_mode,
        )

    # Plan 10 P1：同对话默认续改。业务员心智「我在一个对话里反复优化这个页面」→
    # 同一 conversation 已经有一个可续改的 CR（preview-ready 或 merged + 有 branch）
    # → 把 classifier 判出来的 new_cr 强转 refine_cr。换主题就开新 tab，那是新
    # conversation 自然走 new_cr。chat_only 不受影响。
    if (
        decision.mode == "new_cr"
        and payload.override_mode is None  # 业务员显式 force 不变
        and last_cr is not None
        and last_cr.state in ("preview-ready", "merged")
        and last_cr.branch
    ):
        from orchestrator.intent_classifier import IntentDecision
        decision = IntentDecision(
            mode="refine_cr",
            confidence=max(decision.confidence, 0.85),
            reason=(
                f"同对话已有可续改 CR（{last_cr.state}），按 refine 处理；"
                "想从头开个新方向 → 点 + 新建对话"
            ),
        )

    # 4) dispatch
    if decision.mode == "chat_only":
        if app_state.chat_responder_factory is None:
            raise HTTPException(
                status_code=503, detail="chat_responder_factory 未装配；先在 lifespan 装",
            )
        chat = app_state.chat_responder_factory(db)
        await chat.respond(
            conversation_id=conv_id,
            user_message=payload.text,
            repo_doc=repo_doc if classifier is not None else "",
        )
        # 找 chat append 进去的 ai message id（最后一条 ai 类型 message）
        db.expire_all()
        fresh = conv_repo.get(conv_id)
        ai_msg_id = None
        for m in reversed(list(fresh.messages or [])):
            if m.get("type") == "ai":
                ai_msg_id = m.get("id") or _uuid.uuid4().hex
                break
        return PostMessageOut(
            message_id=user_msg_id,
            mode="chat_only",
            ai_message_id=ai_msg_id,
            confidence=decision.confidence,
            is_unsure=decision.is_unsure,
            reason=decision.reason,
        )

    # new_cr / refine_cr：建 CR + spawn pipeline
    # url / viewport / box 在 chat 入口路径上不强求，attachments 里若有 framed_region
    # 才会带；老 single screenshot_b64 字段不再用。
    primary_url = ""
    primary_box: dict = {}
    primary_viewport: dict = {}
    if payload.attachments:
        for a in payload.attachments:
            if a.kind == "framed_region":
                primary_url = a.url or ""
                primary_box = a.box or {}
                primary_viewport = a.viewport or {}
                break
    raw = RawRequest(
        url=primary_url,
        screenshot_b64="",  # 多图模式不用单字段
        box_coords=primary_box,
        viewport=primary_viewport,
        request_text=payload.text,
        attachments=[a.model_dump() for a in (payload.attachments or [])],
    )
    cr = cr_repo.create(raw, conversation_id=conv_id)
    cr_repo.set_mode(cr.id, decision.mode)
    if decision.mode == "refine_cr":
        if last_cr is None:
            raise HTTPException(
                status_code=409,
                detail="refine_cr 需要上一个 CR，但 conversation 还没有 CR",
            )
        cr_repo.set_refine_of(cr.id, last_cr.id)

    # 反查 user message 关联到新 CR（方便 UI 把 message 跟 cr 关起来）
    fresh = conv_repo.get(conv_id)
    msgs = list(fresh.messages or [])
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("id") == user_msg_id:
            msgs[i]["cr_id"] = cr.id
            msgs[i]["cr_mode"] = decision.mode
            break
    fresh.messages = msgs
    db.commit()

    pipeline = app_state.build_pipeline(db)
    app_state.pipelines[cr.id] = pipeline
    _spawn(pipeline.run(cr.id, mode=decision.mode))
    return PostMessageOut(
        message_id=user_msg_id,
        mode=decision.mode,
        cr_id=cr.id,
        confidence=decision.confidence,
        is_unsure=decision.is_unsure,
        reason=decision.reason,
    )


@app.post("/change-requests/{request_id}/answer")
def submit_answer(request_id: str, payload: AnswerIn) -> dict[str, str]:
    pipeline = app_state.pipelines.get(request_id)
    if pipeline is None:
        raise HTTPException(status_code=409, detail="无活跃流水线")
    try:
        channel = pipeline.channel_for(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="该请求当前不在澄清阶段")
    channel.submit_answer(payload.question_id, payload.answer)
    return {"status": "ok"}


@app.post("/change-requests/{request_id}/runtime-errors")
async def report_runtime_errors(
    request_id: str, payload: RuntimeErrorsIn, db: Session = Depends(get_db),
) -> dict:
    """浏览器侧 content script 捕到的 React 运行时错误回灌。

    用 ChangeRequest.self_heal_attempts 做幂等 + 限次：
    - state 必须是 PREVIEW_READY（其他状态 ignore，避免污染 fail 状态）
    - self_heal_attempts >= 1 → 不再触发自愈（一次足矣，多次只会越改越乱）
    - 否则 ++attempts，把 errors 拼成 hint 推 SSE log，触发 dev_runner 改一轮
    """
    repo = ChangeRequestRepository(db)
    cr = repo.get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="变更请求不存在")
    if not payload.errors:
        return {"status": "no-errors", "will_self_heal": False}
    if cr.state != State.PREVIEW_READY.value:
        return {"status": "wrong-state", "will_self_heal": False}
    if (cr.self_heal_attempts or 0) >= 1:
        return {"status": "max-attempts-reached", "will_self_heal": False}

    bus = app_state.event_bus
    err_lines = [f"⚠ 检测到 {len(payload.errors)} 个运行时错误，自动尝试修复："]
    for e in payload.errors[:5]:
        err_lines.append(f"  · {e.message[:200]}")
        if e.stack:
            first_frames = e.stack.split("\n")[0:2]
            for f in first_frames:
                err_lines.append(f"    {f.strip()[:200]}")
    for line in err_lines:
        await bus.publish_log(request_id, "self-heal", line)

    try:
        cr.self_heal_attempts = (cr.self_heal_attempts or 0) + 1
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()

    _spawn(_run_self_heal(request_id, [e.model_dump() for e in payload.errors]))
    return {"status": "self-healing", "will_self_heal": True}


async def _run_self_heal(request_id: str, errors: list[dict]) -> None:
    """Self-heal v2：把 runtime 错误作为 hint，让 dev_runner 在同 branch 续改一轮。

    交给 pipeline.run_self_heal 做实际工作（locate + context_pack + dev_runner.run
    + refresh_files）；这里只负责拿一只 fresh DB session 给 build_pipeline 用，
    以及做表层异常兜底（self-heal 失败绝不影响业务员既有的 preview）。
    """
    bus = app_state.event_bus
    from orchestrator.db import SessionLocal
    session_factory = app_state.session_factory or SessionLocal
    db = session_factory()
    try:
        pipeline = app_state.build_pipeline(db)
        await pipeline.run_self_heal(request_id, errors)
    except Exception:  # noqa: BLE001
        logger.warning("self-heal failed for cr=%s", request_id, exc_info=True)
        try:
            await bus.publish_log(
                request_id, "self-heal", "⚠ self-heal 异常（不影响现有 preview）",
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


@app.post("/change-requests/{request_id}/merge", response_model=ChangeRequestOut)
async def merge_change_request(
    request_id: str, db: Session = Depends(get_db)
) -> ChangeRequestOut:
    repo = ChangeRequestRepository(db)
    cr = repo.get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="变更请求不存在")
    if cr.state != State.PREVIEW_READY.value:
        raise HTTPException(status_code=409, detail="只有 preview-ready 才能合并")
    gm = GitManager(settings.demo_repo_path)
    try:
        gm.merge_to_main(cr.branch)
    except GitConflictError as exc:
        repo.mark_failed(
            request_id, phase="merging", reason="conflict", log=str(exc)
        )
        app_state.quota.release(request_id)
        await app_state.event_bus.publish(
            request_id,
            Event(type="status", data={
                "state": State.FAILED.value,
                "phase": "merging",
                "reason": "conflict",
            }),
        )
        return ChangeRequestOut.from_model(repo.get(request_id))
    repo.transition(request_id, State.MERGED)
    app_state.quota.release(request_id)
    # 关键：广播 MERGED status —— 旧版只 transition DB 不发事件，扩展端 mirror
    # 永远停在 preview-ready，业务员再点合并就 409「已是终态」+ UI 静默无反应。
    await app_state.event_bus.publish(
        request_id,
        Event(type="status", data={"state": State.MERGED.value}),
    )
    # 后台异步：（1）拆掉本 CR 的预览容器（merged 后留着是泄漏）；（2）重建 main-demo。
    if cr.preview_handle:
        _spawn(_teardown_preview_after_terminal(request_id, cr.preview_handle))
    if settings.main_demo_refresh_script:
        _spawn(_refresh_main_demo(request_id, settings.main_demo_refresh_script))
    return ChangeRequestOut.from_model(repo.get(request_id))


async def _teardown_preview_after_terminal(
    request_id: str, handle: str, phase: str = "merged",
) -> None:
    """终态（merged/discarded/failed-with-preview）后拆掉预览容器，避免 5100~5199
    端口段被废弃容器占满。reaper 只清 preview-ready 状态，所以这里兜底。"""
    bus = app_state.event_bus
    if app_state.preview is None:
        return
    try:
        from orchestrator.adapters.types import PreviewInstance
        instance = PreviewInstance(preview_id=handle, url="", handle=handle)
        await app_state.preview.teardown(instance)
        await bus.publish_log(request_id, phase, f"✓ 已拆掉预览容器 {handle[:12]}")
    except Exception as exc:  # noqa: BLE001
        await bus.publish_log(
            request_id, phase, f"⚠ 拆预览容器失败（不阻塞）：{exc}",
        )


async def _refresh_main_demo(request_id: str, script_path: str) -> None:
    """异步跑 main-demo 重建脚本，把 stdout 行级回灌到 CR 的 SSE log。"""
    bus = app_state.event_bus

    async def _log(line: str) -> None:
        await bus.publish_log(request_id, "merged", line)

    await _log("▸ 重建 main demo（编译新前端 + 重启容器）...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", script_path, "--rebuild",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        await _log(f"✗ 找不到刷新脚本：{exc}")
        return
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if text.strip():
            await _log(text)
    rc = await proc.wait()
    if rc == 0:
        await _log("✓ main demo 已刷新，回到 :5199 刷新页面就能看到改动")
    else:
        await _log(f"✗ main demo 刷新失败 rc={rc}；可手动跑 deploy/main-demo.sh --rebuild")


@app.post("/change-requests/{request_id}/discard", response_model=ChangeRequestOut)
async def discard_change_request(
    request_id: str, db: Session = Depends(get_db)
) -> ChangeRequestOut:
    repo = ChangeRequestRepository(db)
    cr = repo.get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="变更请求不存在")
    if State(cr.state) in TERMINAL:
        raise HTTPException(status_code=409, detail="请求已是终态")
    if cr.branch:
        GitManager(settings.demo_repo_path).delete_branch(cr.branch)
    repo.transition(request_id, State.DISCARDED)
    app_state.quota.release(request_id)
    # 与 merge 对称：广播状态 + 异步拆预览容器，避免扩展端 mirror 卡 preview-ready
    # 加上端口段被泄漏容器吃满。
    await app_state.event_bus.publish(
        request_id,
        Event(type="status", data={"state": State.DISCARDED.value}),
    )
    if cr.preview_handle:
        _spawn(_teardown_preview_after_terminal(
            request_id, cr.preview_handle, phase="discarded",
        ))
    return ChangeRequestOut.from_model(repo.get(request_id))


@app.post("/change-requests/{request_id}/retry", response_model=ChangeRequestOut)
async def retry_change_request(
    request_id: str, db: Session = Depends(get_db)
) -> ChangeRequestOut:
    repo = ChangeRequestRepository(db)
    cr = repo.get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="变更请求不存在")
    if State(cr.state) not in {State.FAILED, State.EXPIRED}:
        raise HTTPException(status_code=409, detail="只有 failed/expired 才能重试")
    raw = RawRequest(
        url=cr.url,
        screenshot_b64=cr.screenshot_b64,
        box_coords=cr.box_coords,
        viewport=cr.viewport,
        request_text=cr.request_text,
        attachments=list(cr.attachments or []),
    )
    new_cr = repo.create(raw, retry_of=cr.id)
    pipeline = app_state.build_pipeline(db)
    app_state.pipelines[new_cr.id] = pipeline
    _spawn(pipeline.run(new_cr.id))
    return ChangeRequestOut.from_model(new_cr)


@app.get("/change-requests/{request_id}/events")
async def change_request_events(request_id: str):
    async def event_stream():
        async for evt in app_state.event_bus.subscribe(request_id):
            yield {"event": evt.type, "data": json.dumps(evt.data)}

    return EventSourceResponse(event_stream())


# ── Plan 9: Conversation REST ────────────────────────────────────────
@app.post("/conversations")
def create_conversation(payload: dict | None = None, db: Session = Depends(get_db)) -> dict:
    """POST {title?: str} → 创建空对话，返回 {id, title, messages: []}"""
    title = (payload or {}).get("title", "") if isinstance(payload, dict) else ""
    conv = ConversationRepository(db).create(title=title)
    return _conv_out(conv)


@app.get("/conversations")
def list_conversations(archived: bool = False, db: Session = Depends(get_db)) -> dict:
    """列出对话 —— 只返元数据（id/title/timestamps），messages 留给 GET 单条取。

    历史教训：早先返完整 messages JSON 列时撞 MySQL sort_buffer 限制
    （1038 Out of sort memory），整个端点 500。前端 tab 命名 + 历史列表都炸。
    """
    repo = ConversationRepository(db)
    items = repo.list_meta(include_archived=archived)
    return {"items": items}


@app.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, db: Session = Depends(get_db)) -> dict:
    conv = ConversationRepository(db).get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation 不存在")
    return _conv_out(conv)


@app.post("/conversations/{conv_id}/archive")
def archive_conversation(conv_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        conv = ConversationRepository(db).archive(conv_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="conversation 不存在")
    return _conv_out(conv)


def _conv_out(conv) -> dict:
    return {
        "id": conv.id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        "archived_at": conv.archived_at.isoformat() if conv.archived_at else None,
        "messages": conv.messages or [],
    }


# ── Plan 11 · M1.T4 多仓 sync 端点 ─────────────────────────────────


class RepoSpecIn(BaseModel):
    url: str = Field(min_length=1, description="git URL：https://github.com/o/r 或 git@github.com:o/r")
    main_branch: str = Field(default="main", min_length=1, description="rebase 起点")
    target_branch: str = Field(
        default="vibe-niuma/dev", min_length=1,
        description="业务员 CR 合并目标分支；程序员从这条分支提 PR 到 main",
    )


class SyncReposIn(BaseModel):
    repos: list[RepoSpecIn] = Field(default_factory=list)
    pat: str | None = Field(
        default=None,
        description="GitHub Personal Access Token（不入 DB，仅本次请求用）",
    )


@app.post("/projects/{project_id}/sync-repos")
async def sync_project_repos(project_id: str, payload: SyncReposIn) -> dict:
    """业务员配完仓库后，扩展调这个端点：
    - 不存在 → clone 到 <workspaces_root>/<project_id>/<repo_name>/
    - 已存在 → git fetch + reset --hard origin/<target_branch>
    - target_branch 不存在 remote → 从 mainBranch 切 + push -u
    全程幂等。一个仓挂掉不影响其他仓。
    """
    # project_id 仅做路径安全校验（防 ../../ 跑出 workspaces_root）
    safe = project_id.replace("/", "_").replace("..", "_")
    if not safe or safe != project_id:
        raise HTTPException(status_code=400, detail=f"非法 project_id: {project_id!r}")

    specs = [
        RepoSpec(url=r.url, main_branch=r.main_branch, target_branch=r.target_branch)
        for r in payload.repos
    ]
    workspaces_root = Path(settings.workspaces_root)
    result = await run_sync_repos(
        specs,
        project_id=project_id,
        pat=payload.pat,
        workspaces_root=workspaces_root,
    )
    return {
        "project_id": project_id,
        "synced": [
            {
                "name": s.name,
                "url": s.url,
                "work_dir": s.work_dir,
                "head_sha": s.head_sha,
                "target_branch": s.target_branch,
                "target_branch_created": s.target_branch_created,
            }
            for s in result.synced
        ],
        "failed": [
            {"url": f.url, "error_kind": f.error_kind, "error_message": f.error_message}
            for f in result.failed
        ],
    }
