"""API 出入参。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectOut(BaseModel):
    id: str
    name: str
    slug: str
    target_branch: str
    repos: list[str] = []
    quota_parallel_runs: int
    active_requirements: int = 0
    awaiting_review: int = 0


class RequirementIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    attachments: list[dict] = []


class TaskOut(BaseModel):
    id: str
    key: str
    title: str
    repos: list[str] = []
    depends_on: list[str] = []
    touches: list[str] = []
    sequence: str | None = None
    state: str
    # 失败时给人看的原因。没有它，界面上任务就只是「failed」两个字。
    fail_reason: str = ""
    attempts: int = 0


class RequirementOut(BaseModel):
    id: str
    ref: str
    title: str
    body: str
    requested_by: str
    stage: str
    state: str
    contracts: list[str] = []
    sequence_kind: str | None = None
    tasks: list[TaskOut] = []
    # AI 在等这条需求的提出人回话 —— 看板上要显眼，否则需求会静静地卡住
    awaiting_answer: bool = False
    created_at: datetime


class FindingOut(BaseModel):
    id: str
    axis: str
    severity: str
    category: str
    path: str
    start_line: int
    claim: str
    failure_scenario: str = ""
    kept: bool
    confidence: str = ""
    verdict_reason: str = ""


class MessageOut(BaseModel):
    id: str
    role: str          # user | agent | system
    author: str
    body: str
    stage: str
    awaiting_answer: bool
    # 这条消息背后 agent 的思考过程 —— 前端可展开看
    trace: list[dict] = []
    created_at: datetime


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    # 「够了直接干」—— 不再等 AI 追问，按现有信息开工
    proceed: bool = False


class ActivityOut(BaseModel):
    """流水线活动记录 —— SSE 的历史版本。

    之前只有实时流：中途打开页面的人看不到已经发生过的事，
    需求失败了界面上一片空白。"""

    id: int
    kind: str
    stage: str = ""
    state: str = ""
    detail: str = ""
    created_at: datetime


class PreviewOut(BaseModel):
    """一条需求的预览地址（每个并行分支一个）。"""

    branch: str
    task_key: str = ""
    url: str


class IntakeIn(BaseModel):
    """开始立需求 —— 只要一句大白话，剩下的跟 AI 谈。"""

    opening: str = Field(min_length=1, max_length=8000)


class DraftEditIn(BaseModel):
    """确认之前，人可以直接改需求稿。"""

    title: str | None = Field(default=None, max_length=300)
    body: str | None = None


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|changes)$")
    comment: str = ""


class MergeJobOut(BaseModel):
    id: str
    requirement_ref: str
    repo_name: str
    position: int
    state: str
    conflict_ladder: list[dict] = []


class EnvOut(BaseModel):
    env: str
    state: str = "unknown"
    url: str | None = None
    finished_at: datetime | None = None
