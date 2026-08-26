"""多租户数据模型（§4）。

三条硬约束：
1. **每张业务表带 project_id** —— 租户隔离落在 schema + 索引层，不靠应用层记得加 WHERE
2. **Run 是幂等边界** —— 重试 = 新建 Run，不复用；Workspace 与 Run 1:1
3. **密钥不落明文** —— 只存引用（secret_ref），实际值走环境/密钥管理

数据库是 MySQL 8（D4）。大文本用 Text().with_variant(LONGTEXT, "mysql")，
sqlite 测试仍走 Text。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    event,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME, LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_BIG = Text().with_variant(LONGTEXT, "mysql")

# sqlite 只把 `INTEGER PRIMARY KEY` 当 rowid 别名做自增，BIGINT 不行。
# MySQL 上要 BIGINT（INT 的 21 亿上限在事件表上约两年就触顶）。
_BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")

# **必须带微秒精度。**
#
# MySQL 的无精度 DATETIME 会把小数秒**四舍五入**：11:59:21.600 存进去变成
# 11:59:22。于是 `enqueue` 写的 next_run_at 可能比真实时间晚将近一秒，
# worker 的 `next_run_at <= now` 判不成立 —— **job 隐身最多 500ms**，
# 约四成的 job 会这样。
#
# 这直接打破「交互 lane 200ms 秒回」的承诺：人点了审核通过，
# 信号把 next_run_at 置为 now，却被进位到未来，看起来就是卡住。
# sqlite 上完全看不到这个问题（它存完整 ISO 串），实测在 MySQL 9.3 上复现。
_TS = DateTime().with_variant(MYSQL_DATETIME(fsp=6), "mysql")


def _uid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


@event.listens_for(Base, "init", propagate=True)
def _assign_id_at_construction(target, args, kwargs) -> None:
    """**构造时就生成 id**，不等 flush。

    为什么：`default=_uid` 是列默认值，SQLAlchemy 只在 INSERT 时应用它 ——
    `session.add(obj)` 之后 `obj.id` 仍是 None。把它带进 job payload / 事件流 /
    外键就是一个 None，而且要到很远的地方才炸（我们已经被咬过一次）。

    用 `init` 事件而不是覆盖 `__init__` —— 后者会绕过声明式构造器。
    只管 String 主键；Event.id 是自增整数，交给数据库。
    """
    if kwargs.get("id"):
        return
    col = type(target).__table__.c.get("id")
    if col is not None and col.primary_key and isinstance(col.type, String):
        kwargs["id"] = _uid()


class TenantMixin:
    """所有业务表继承它。project_id 是租户隔离键，禁止裸查。"""

    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id"), nullable=False, index=True
    )


# ── 租户 ────────────────────────────────────────────────────────────
class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)


class Project(Base):
    """一个空间 = 一个产品 = 一套仓库 + 一条流水线 + 一个需求池。

    取代 v1 的 SystemConfig 单例（D5）。
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    org_id: Mapped[str] = mapped_column(String(32), ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # 汇流分支 —— 审核通过的需求合并到这里，不是直接进 main
    target_branch: Mapped[str] = mapped_column(String(120), default="vibe/dev")

    # 模型 / runner
    dev_runner: Mapped[str] = mapped_column(String(32), default="opencode")
    dev_model: Mapped[str] = mapped_column(String(128), default="deepseek-v4-pro")
    review_model: Mapped[str] = mapped_column(String(128), default="deepseek-v4-pro")

    # 配额（§5.3 坑 2：端口是稀缺资源）
    quota_parallel_runs: Mapped[int] = mapped_column(Integer, default=8)
    port_min: Mapped[int] = mapped_column(Integer, default=5100)
    port_max: Mapped[int] = mapped_column(Integer, default=5199)
    token_budget_per_run: Mapped[int] = mapped_column(Integer, default=200_000)

    workspaces_root: Mapped[str] = mapped_column(String(512), default="/data/projects")

    # **密钥只存引用**，不存明文。形如 {"llm": "env:DASHSCOPE_API_KEY"}
    secret_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    # 非密配置：pipeline 名、skill 覆盖等
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    # 每个空间独立的需求编号计数器。R-142 是空间内编号，不是全局。
    # 取号靠 `UPDATE projects SET req_seq = req_seq + 1 WHERE id=?` 原子自增，
    # 不用 SELECT MAX(seq)（并发下会重号）。
    req_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    version: Mapped[int] = mapped_column(Integer, default=0)  # 乐观锁
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=_now, onupdate=_now)


class ProjectRepo(Base, TenantMixin):
    __tablename__ = "project_repos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    host_kind: Mapped[str] = mapped_column(String(24), default="github")  # github|codeup|gitee
    default_branch: Mapped[str] = mapped_column(String(120), default="main")
    # PAT 引用，不存明文
    pat_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_repo_per_project"),)


class Member(Base, TenantMixin):
    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    # requester 提需求 / reviewer 可审核 / admin 可改配置
    role: Mapped[str] = mapped_column(String(24), default="requester")
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_member"),)


# ── 需求 → 任务 → 执行 ──────────────────────────────────────────────
class Requirement(Base, TenantMixin):
    """一条需求 = 一个隔离单元。取代 v1 的 ChangeRequest。"""

    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    # 人读编号 R-xxx。**不能用 autoincrement** —— MySQL 要求 AUTO_INCREMENT 必须是键，
    # 且我们要的是每空间独立编号。由 next_requirement_seq() 原子取号。
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(_BIG, default="")
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)

    # 流水线位置：stage key（§7.4 DAG 里的名字），不是硬编码枚举
    stage: Mapped[str] = mapped_column(String(40), default="triage", index=True)
    state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    # active | awaiting_signal | done | failed | discarded

    # 拆解产物
    contracts: Mapped[list] = mapped_column(JSON, default=list)
    # wide refactor（§8.4）：expand|migrate|contract 序列的标记
    sequence_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)

    attachments: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_req_project_stage", "project_id", "stage"),
        UniqueConstraint("project_id", "seq", name="uq_req_seq_per_project"),
    )

    @property
    def ref(self) -> str:
        return f"R-{self.seq}"


class Message(Base, TenantMixin):
    """需求上的对话 —— 澄清问答、续改反馈、系统通告。

    之前完全没有这层：`clarify` 环节在 DAG 里是空转的，
    也没有任何「回答澄清」的入口。用户提完需求就只能干等，
    一句话都插不进去。而 v1 的卖点之一恰恰是「真多轮澄清」。
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    requirement_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("requirements.id"), nullable=False, index=True
    )
    # user 人说的 / agent AI 说的 / system 平台通告
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    author: Mapped[str] = mapped_column(String(120), default="")
    body: Mapped[str] = mapped_column(_BIG, default="")
    # 这条消息发生在哪个环节 —— 回看时能对上流水线位置
    stage: Mapped[str] = mapped_column(String(40), default="")
    # agent 提问时挂的待答问题；人回答后置 False
    awaiting_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    # agent 产出这条消息时的思考过程（工具调用 / 中间输出）。
    # 实时流是过程、进程重启即丢；这一份是**真相**，刷新页面还能展开看。
    trace: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)

    __table_args__ = (Index("ix_msg_req", "requirement_id", "created_at"),)


class Task(Base, TenantMixin):
    """AI 拆出的并行子任务（to-tickets 的 ticket）。"""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    requirement_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("requirements.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(16), nullable=False)  # T1 / T2
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    delivers: Mapped[str] = mapped_column(Text, default="")
    repo_names: Mapped[list] = mapped_column(JSON, default=list)
    # blocking edges —— to-tickets 的 "Blocked by"
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    # expand | migrate | contract（wide refactor 序列，§8.4）
    sequence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)


class TaskTouch(Base, TenantMixin):
    """任务预计触达的路径 —— §7.5 补偿设计 ③。

    **不用 JSON 数组**：MySQL 没有 JSONB+GIN，交集查询走不了索引。
    规范化成关联表后，「哪些 in-flight 需求与本任务触达同一文件」就是一次普通 JOIN。

    这也是 §8.3 保险 ① 的数据基础：
      - 同需求内 touches 相交的 task 不并行
      - 跨需求相交 → 合并队列排序 + 提前预警
    """

    __tablename__ = "task_touches"

    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.id"), primary_key=True
    )
    # **400 而不是 512**：这一列同时进主键和 ix_touch_path。
    # utf8mb4 下每字符 4 字节，InnoDB DYNAMIC 的索引上限是 3072 字节。
    # 512 时 ix_touch_path = 32*4 + 120*4 + 512*4 = 2656 字节，贴边；
    # 一旦有人把 repo_name 放宽、或库跑在 COMPACT row_format / MySQL 5.7
    # 兼容模式下，建表会直接失败。400 留出余量（= 2208 字节）。
    # 仓库里的路径极少超过 400 字符。
    path: Mapped[str] = mapped_column(String(400), primary_key=True)
    repo_name: Mapped[str] = mapped_column(String(120), default="")

    __table_args__ = (
        Index("ix_touch_path", "project_id", "repo_name", "path"),
        {"mysql_row_format": "DYNAMIC"},      # 显式声明，不靠服务端默认值
    )


class Run(Base, TenantMixin):
    """一次执行 —— **幂等边界**。重试 = 新建 Run，不复用。"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("tasks.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    branch: Mapped[str] = mapped_column(String(255), default="")
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    commit_shas: Mapped[dict] = mapped_column(JSON, default=dict)  # {repo: sha}
    fail_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fail_log: Mapped[str | None] = mapped_column(_BIG, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)


class Workspace(Base, TenantMixin):
    """worktree + 容器，与 Run 1:1。Run 终结即回收（§5）。"""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id"), unique=True)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image: Mapped[str | None] = mapped_column(String(256), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="acquiring", index=True)
    # acquiring | ready | releasing | released | failed
    repos: Mapped[dict] = mapped_column(JSON, default=dict)  # {repo_name: worktree_path}
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)
    released_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class PortLease(Base, TenantMixin):
    """端口租约 —— §5.3 坑 2。

    v1 硬编码全局 5100-5199，两个 worker 会分到同一个端口。
    唯一索引 (project_id, port) 让抢占在 DB 层就失败，不靠应用层协调。
    """

    __tablename__ = "port_leases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    leased_at: Mapped[datetime] = mapped_column(_TS, default=_now)
    expires_at: Mapped[datetime] = mapped_column(_TS, nullable=False)

    __table_args__ = (UniqueConstraint("project_id", "port", name="uq_port_per_project"),)


class AgentSession(Base, TenantMixin):
    """opencode 会话 —— **独立于 Run 存在**（§4.1）。

    这是 v1 头号问题（P1）的落点：
      - refine 续改 → 复用 session_id 走 resume，不重建上下文
      - 拆并行子任务 → 从父 session fork
    """

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    requirement_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="opencode")
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    purpose: Mapped[str] = mapped_column(String(32), default="code")  # plan|code|review|conflict
    # **会话绑定目录。**
    # opencode 的会话记着自己的工作目录，fork 出来的子会话也继承它。
    # 拆解会话在 plan 工位、实现任务在各自的 run 工位 —— 跨目录 fork 会报
    # `Failed to init file picker: Invalid path`（那个工位已经被回收了）。
    # 所以要记下目录，只在同目录时才 fork。
    cwd: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)


# ── 审核 / 合并 / 部署 ──────────────────────────────────────────────
class Finding(Base, TenantMixin):
    """复核发现 —— 缺陷轴（ocr）+ 规格轴/规范轴（code-review skill）。"""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id"), index=True)
    axis: Mapped[str] = mapped_column(String(16), default="defect")  # defect|spec|norm
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    category: Mapped[str] = mapped_column(String(48), default="")
    path: Mapped[str] = mapped_column(String(512), default="")
    start_line: Mapped[int] = mapped_column(Integer, default=0)
    end_line: Mapped[int] = mapped_column(Integer, default=0)
    claim: Mapped[str] = mapped_column(Text, default="")
    failure_scenario: Mapped[str] = mapped_column(Text, default="")
    existing_code: Mapped[str] = mapped_column(Text, default="")
    suggestion_code: Mapped[str] = mapped_column(Text, default="")
    # 自建过滤层的裁决（§9.10 第一层）
    kept: Mapped[bool] = mapped_column(Boolean, default=True)
    verdict_reason: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)


class Review(Base, TenantMixin):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    requirement_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirements.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)  # approve|reject|changes
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)


class MergeJob(Base, TenantMixin):
    """合并队列条目 —— per-repo 串行（§12）。"""

    __tablename__ = "merge_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    requirement_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirements.id"), index=True)
    repo_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    # queued | rebasing | conflict | resolving | verifying | merged | rejected
    # 三档冲突处理的进展：[{stage, ok, detail}]
    conflict_ladder: Mapped[list] = mapped_column(JSON, default=list)
    merged_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=_now, onupdate=_now)


class DeployRun(Base, TenantMixin):
    """DeployAdapter 的执行记录 —— M1 就建表，避免 M7 二次迁移（D10）。"""

    __tablename__ = "deploy_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    env: Mapped[str] = mapped_column(String(24), nullable=False, index=True)  # preview|test|prod
    ref: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter: Mapped[str] = mapped_column(String(32), default="selfhosted")
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    # queued | running | succeeded | failed | cancelled
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)


# ── 编排（§7）────────────────────────────────────────────────────────
class Job(Base, TenantMixin):
    """worker 的工作单元。

    MySQL 没有部分索引 → §7.5 补偿设计 ②：终态行由 reaper 搬到 jobs_archive，
    本表只留活跃行。索引 (state, next_run_at) 够用。
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    requirement_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    # 交互类（人刚点了按钮，要秒回）走 200ms 轮询；后台类 2s 起退避（§7.5 ①）
    lane: Mapped[str] = mapped_column(String(16), default="background", index=True)
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    # pending | running | awaiting_signal | done | failed
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_run_at: Mapped[datetime] = mapped_column(_TS, default=_now)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=_now, onupdate=_now)

    __table_args__ = (Index("ix_job_claim", "state", "next_run_at"),)


class ApiToken(Base):
    """API token。**只存哈希** —— 泄库拿不到可用凭证。

    定义放在这里而不是 api/auth.py：模型必须全部挂在 core.models 上，
    否则 `create_all()` 跑的时候那个模块可能还没被 import，表就不会建
    （实测踩过：签发 token 时报 "no such table: api_tokens"）。
    """

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class JobArchive(Base, TenantMixin):
    """终态 job 的归档表（§7.5 补偿设计 ②）。

    MySQL 没有部分索引，jobs 表里 99% 是终态行。热表只留活跃行，
    终态行由 reaper 搬到这里 —— 同时解决索引大小和表膨胀两个问题
    （Postgres 的部分索引只解决前者）。
    """

    __tablename__ = "jobs_archive"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    requirement_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    lane: Mapped[str] = mapped_column(String(16), default="background")
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS)
    archived_at: Mapped[datetime] = mapped_column(_TS, default=_now)


class Step(Base, TenantMixin):
    """job 内的一步。output 落库 = 重放时跳过（Temporal replay 的最小自建版）。"""

    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("jobs.id"), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)

    __table_args__ = (UniqueConstraint("job_id", "name", name="uq_step_per_job"),)


class Signal(Base, TenantMixin):
    """人工 gate 的唤醒信号（§7.3 ③）。

    MySQL 没有 LISTEN/NOTIFY → 写 signal 的同时把目标 job 的 next_run_at 置为 now，
    下一轮轮询捡到。交互 lane 200ms，人无感。
    """

    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("jobs.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)


class Event(Base, TenantMixin):
    """事件总线的持久层（§13）。自增 id 支撑 last_event_id 断线重放。

    实时 fan-out 走 Redis Streams；这张表负责真相与回放。
    """

    __tablename__ = "events"

    # **BigInteger 而不是 Integer。**
    # MySQL 的 INT 是 21 亿上限；agent 日志「每秒几十行」的量级下，
    # 单实例约两年就触顶，而这个 id 是 SSE 断线重放的锚点 —— 溢出等于
    # 整个事件回放机制失效。
    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    stream: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_TS, default=_now)

    __table_args__ = (Index("ix_event_replay", "stream", "id"),)


def next_requirement_seq(session, project_id: str) -> int:
    """为空间取下一个需求编号，原子。

    用 `UPDATE ... SET req_seq = req_seq + 1` 让数据库做自增，而不是
    `SELECT MAX(seq) + 1` —— 后者在并发下会重号，而 seq 上有唯一约束，
    重号会变成插入失败。
    """
    from sqlalchemy import select, update

    session.execute(
        update(Project).where(Project.id == project_id).values(req_seq=Project.req_seq + 1)
    )
    return int(session.execute(select(Project.req_seq).where(Project.id == project_id)).scalar_one())
