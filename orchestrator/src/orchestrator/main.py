"""FastAPI app —— REST + SSE 端点、依赖装配、lifespan。

Plan 2 用 fake adapter 装配（见 AppState.build_pipeline）；Plan 3 在 build_pipeline
里换成真实 adapter —— 那是 Plan 3 唯一的接线改动点。
"""
import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from orchestrator.adapters.fakes import (
    FakeDevRunner,
    FakeInteractionSkill,
    FakePreviewAdapter,
    FakeStackAdapter,
)
from orchestrator.adapters.types import RawRequest
from orchestrator.config import settings
from orchestrator.db import Base, engine, get_db
from orchestrator.events import EventBus
from orchestrator.git_manager import GitConflictError, GitManager
from orchestrator.pipeline import Pipeline
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.schemas import AnswerIn, ChangeRequestOut, CreateChangeRequestIn
from orchestrator.states import TERMINAL, State


class AppState:
    """进程内单例：事件总线、配额、Pipeline、后台任务集、可注入 session factory。"""

    def __init__(self) -> None:
        self.event_bus = EventBus()
        self.quota = QuotaManager(capacity=settings.quota_size)
        self.pipeline: Pipeline | None = None
        self.tasks: set[asyncio.Task] = set()
        self.session_factory = None

    def build_pipeline(self, db: Session) -> Pipeline:
        return Pipeline(
            repo_path=settings.demo_repo_path,
            repository=ChangeRequestRepository(db),
            git_manager=GitManager(settings.demo_repo_path),
            event_bus=self.event_bus,
            quota=self.quota,
            interaction_skill=FakeInteractionSkill(question_count=0),
            stack_adapter=FakeStackAdapter(),
            dev_runner=FakeDevRunner(),
            preview_adapter=FakePreviewAdapter(),
        )


app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
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
    finally:
        db.close()

    # 启动闲置回收后台循环
    reaper_task = asyncio.create_task(
        run_reaper_loop(
            session_factory=session_factory,
            quota=app_state.quota,
            preview_adapter=FakePreviewAdapter(),
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


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    app_state.tasks.add(task)
    task.add_done_callback(app_state.tasks.discard)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    cr = repo.create(raw)
    pipeline = app_state.build_pipeline(db)
    app_state.pipeline = pipeline
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
    if app_state.pipeline is None:
        raise HTTPException(status_code=409, detail="无活跃流水线")
    try:
        channel = app_state.pipeline.channel_for(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="该请求当前不在澄清阶段")
    channel.submit_answer(payload.question_id, payload.answer)
    return {"status": "ok"}


@app.post("/change-requests/{request_id}/merge", response_model=ChangeRequestOut)
def merge_change_request(
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
        return ChangeRequestOut.from_model(repo.get(request_id))
    repo.transition(request_id, State.MERGED)
    app_state.quota.release(request_id)
    return ChangeRequestOut.from_model(repo.get(request_id))


@app.post("/change-requests/{request_id}/discard", response_model=ChangeRequestOut)
def discard_change_request(
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
    app_state.pipeline = pipeline
    _spawn(pipeline.run(new_cr.id))
    return ChangeRequestOut.from_model(new_cr)


@app.get("/change-requests/{request_id}/events")
async def change_request_events(request_id: str):
    async def event_stream():
        async for evt in app_state.event_bus.subscribe(request_id):
            yield {"event": evt.type, "data": json.dumps(evt.data)}

    return EventSourceResponse(event_stream())
