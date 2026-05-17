"""ChangeRequest 仓储 —— MySQL 持久化 + 受 FSM 约束的状态转换。"""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.adapters.types import RawRequest
from orchestrator.models import ChangeRequest
from orchestrator.states import TERMINAL, State, is_valid_transition


class ChangeRequestRepository:
    def __init__(self, db: Session):
        self._db = db

    def create(
        self,
        raw: RawRequest,
        retry_of: str | None = None,
        conversation_id: str | None = None,
    ) -> ChangeRequest:
        cr = ChangeRequest(
            id=uuid.uuid4().hex,
            url=raw.url,
            screenshot_b64=raw.screenshot_b64,
            box_coords=raw.box_coords,
            viewport=raw.viewport,
            request_text=raw.request_text,
            state=State.CREATED.value,
            retry_of=retry_of,
            conversation_id=conversation_id,
            # Plan 10 Task 9：多图持久化（schema 已有 attachments JSON 列）
            attachments=list(raw.attachments) if raw.attachments else None,
        )
        self._db.add(cr)
        self._db.commit()
        self._db.refresh(cr)
        return cr

    def get(self, request_id: str) -> ChangeRequest | None:
        return self._db.get(ChangeRequest, request_id)

    def _get_or_raise(self, request_id: str) -> ChangeRequest:
        cr = self.get(request_id)
        if cr is None:
            raise ValueError(f"change request {request_id} not found")
        return cr

    def transition(self, request_id: str, dst: State) -> ChangeRequest:
        cr = self._get_or_raise(request_id)
        src = State(cr.state)
        if not is_valid_transition(src, dst):
            raise ValueError(f"invalid transition {src.value} → {dst.value}")
        cr.state = dst.value
        cr.last_activity_at = datetime.utcnow()
        self._db.commit()
        self._db.refresh(cr)
        return cr

    def mark_failed(
        self, request_id: str, phase: str, reason: str, log: str
    ) -> ChangeRequest:
        cr = self._get_or_raise(request_id)
        src = State(cr.state)
        if not is_valid_transition(src, State.FAILED):
            raise ValueError(f"cannot fail from {src.value}")
        cr.state = State.FAILED.value
        cr.fail_phase = phase
        cr.fail_reason = reason
        cr.fail_log = log
        cr.last_activity_at = datetime.utcnow()
        self._db.commit()
        self._db.refresh(cr)
        return cr

    def set_branch(self, request_id: str, branch: str) -> None:
        cr = self._get_or_raise(request_id)
        cr.branch = branch
        self._db.commit()

    def set_repos(self, request_id: str, repos: dict[str, str]) -> None:
        """Plan 8 Task 11+12：多仓 CR 把 {repo_name: sha} 落到 ChangeRequest.repos 列。"""
        cr = self._get_or_raise(request_id)
        cr.repos = repos
        cr.last_activity_at = datetime.utcnow()
        self._db.commit()

    def set_preview(self, request_id: str, url: str, handle: str) -> None:
        cr = self._get_or_raise(request_id)
        cr.preview_url = url
        cr.preview_handle = handle
        self._db.commit()

    def set_refine_of(self, request_id: str, base_cr_id: str) -> None:
        """Plan 10 Task 5：标记此 CR 是 refine 上一 CR；同时设 mode='refine_cr'。"""
        cr = self._get_or_raise(request_id)
        cr.refine_of = base_cr_id
        cr.mode = "refine_cr"
        cr.last_activity_at = datetime.utcnow()
        self._db.commit()

    def set_mode(self, request_id: str, mode: str) -> None:
        """Plan 10：显式设 mode（new_cr / refine_cr）。"""
        cr = self._get_or_raise(request_id)
        cr.mode = mode
        self._db.commit()

    def touch_activity(self, request_id: str) -> None:
        cr = self._get_or_raise(request_id)
        cr.last_activity_at = datetime.utcnow()
        self._db.commit()

    def list_by_conversation(self, conversation_id: str) -> list[ChangeRequest]:
        """Plan 10 Task 10：拉一个 conversation 内所有 CR，按 created_at 升序。"""
        stmt = (
            select(ChangeRequest)
            .where(ChangeRequest.conversation_id == conversation_id)
            .order_by(ChangeRequest.created_at.asc())
        )
        return list(self._db.scalars(stmt))

    def latest_in_conversation(self, conversation_id: str) -> ChangeRequest | None:
        """Plan 10 Task 10：拿 conversation 最近一条 CR（intent classifier 用它的 state）。"""
        stmt = (
            select(ChangeRequest)
            .where(ChangeRequest.conversation_id == conversation_id)
            .order_by(ChangeRequest.created_at.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def list_non_terminal(self) -> list[ChangeRequest]:
        terminal_values = [s.value for s in TERMINAL]
        stmt = select(ChangeRequest).where(ChangeRequest.state.notin_(terminal_values))
        return list(self._db.scalars(stmt))

    def list_stale_previews(self, older_than_seconds: int) -> list[ChangeRequest]:
        cutoff = datetime.utcnow() - timedelta(seconds=older_than_seconds)
        stmt = select(ChangeRequest).where(
            ChangeRequest.state == State.PREVIEW_READY.value,
            ChangeRequest.last_activity_at < cutoff,
        )
        return list(self._db.scalars(stmt))

    def list_orphan_previews(self) -> list[ChangeRequest]:
        """终态 CR 但 preview_handle 仍非空 —— 容器泄漏候选。

        正常路径下 merge/discard endpoint 会同步拆容器，但 pipeline 失败
        路径（_PhaseError 后 mark_failed）历史上不拆，导致容器永久占端口。
        reaper 拿这个列表立刻清，不等 idle TTL。
        """
        terminal_values = [s.value for s in TERMINAL]
        stmt = select(ChangeRequest).where(
            ChangeRequest.state.in_(terminal_values),
            ChangeRequest.preview_handle.isnot(None),
        )
        return list(self._db.scalars(stmt))

    def clear_preview(self, request_id: str) -> None:
        """拆完容器后把 preview_handle / preview_url 清空，避免重复 teardown。"""
        cr = self._get_or_raise(request_id)
        cr.preview_handle = None
        cr.preview_url = None
        self._db.commit()
