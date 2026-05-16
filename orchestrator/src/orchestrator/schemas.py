from pydantic import BaseModel


class CreateChangeRequestIn(BaseModel):
    url: str
    screenshot_b64: str
    box_coords: dict
    viewport: dict
    request_text: str
    # Plan 9 Task 3：可选挂到现有 conversation；缺省则 orchestrator 自动 create 一个
    conversation_id: str | None = None


class AnswerIn(BaseModel):
    question_id: str
    answer: str


class ChangeRequestOut(BaseModel):
    id: str
    state: str
    url: str
    request_text: str
    branch: str | None
    preview_url: str | None
    fail_phase: str | None
    fail_reason: str | None
    retry_of: str | None
    conversation_id: str | None = None
    repos: dict | None = None

    @classmethod
    def from_model(cls, cr) -> "ChangeRequestOut":
        return cls(
            id=cr.id,
            state=cr.state,
            url=cr.url,
            request_text=cr.request_text,
            branch=cr.branch,
            preview_url=cr.preview_url,
            fail_phase=cr.fail_phase,
            fail_reason=cr.fail_reason,
            retry_of=cr.retry_of,
            conversation_id=getattr(cr, "conversation_id", None),
            repos=getattr(cr, "repos", None),
        )
