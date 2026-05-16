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

    def touch_activity(self, request_id: str) -> None:
        cr = self._get_or_raise(request_id)
        cr.last_activity_at = datetime.utcnow()
        self._db.commit()

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
