"""FastAPI app —— REST + SSE 端点、依赖装配、lifespan。

Plan 2 用 fake adapter 装配（见 AppState.build_pipeline）；Plan 3 在 build_pipeline
里换成真实 adapter —— 那是 Plan 3 唯一的接线改动点。
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

logger = logging.getLogger("orchestrator.main")

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

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
from orchestrator.pipeline import Pipeline
from orchestrator.quota import QuotaManager
from orchestrator.repo_init import RepoInitializer, RepoInitStatus
from orchestrator.repository import ChangeRequestRepository
from orchestrator.schemas import AnswerIn, ChangeRequestOut, CreateChangeRequestIn
from orchestrator.states import TERMINAL, State


class AppState:
    """进程内单例：事件总线、配额、Pipeline、后台任务集、可注入 session factory。"""

    def __init__(self) -> None:
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

    def build_pipeline(self, db: Session) -> Pipeline:
        return self.pipeline_factory(db)

    def _build_real_pipeline(self, db: Session) -> Pipeline:
        """Plan 3：装配真实 adapter；Plan 5 在 ECS 上跑的就是这套。"""
        if settings.dev_runner == "opencode":
            dev_runner = OpenCodeDevRunner()
        else:
            dev_runner = ClaudeCodeDevRunner()
        return Pipeline(
            repo_path=settings.demo_repo_path,
            repository=ChangeRequestRepository(db),
            git_manager=GitManager(settings.demo_repo_path),
            event_bus=self.event_bus,
            quota=self.quota,
            interaction_skill=BrainstormingSkill(
                LLMClient(),
                repo_initializer=self.repo_initializer,
            ),
            stack_adapter=ReactViteStackAdapter(repo_path=settings.demo_repo_path),
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
        ("change_requests", "repos", "JSON NULL"),
        ("change_requests", "conversation_id", "VARCHAR(32) NULL"),
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
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    raw = RawRequest(
        url=payload.url,
        screenshot_b64=payload.screenshot_b64,
        box_coords=payload.box_coords,
        viewport=payload.viewport,
        request_text=payload.request_text,
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
    repo = ConversationRepository(db)
    items = repo.list_all() if archived else repo.list_active()
    return {"items": [_conv_out(c) for c in items]}


@app.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, db: Session = Depends(get_db)) -> dict:
    conv = ConversationRepository(db).get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation 不存在")
    return _conv_out(conv)


@app.post("/conversations/{conv_id}/messages")
def append_conversation_message(
    conv_id: str, payload: dict, db: Session = Depends(get_db),
) -> dict:
    """append 一条 message。payload = {type: 'user'|'ai'|'summary', content: str, ...}"""
    try:
        conv = ConversationRepository(db).append_message(conv_id, payload)
    except KeyError:
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
