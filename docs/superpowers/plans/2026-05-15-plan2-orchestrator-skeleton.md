# Plan 2: Orchestrator 骨架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 AI 原生低代码平台的 Orchestrator 骨架 —— 一个能用 fake adapter 跑通完整变更请求 FSM 生命周期（`created → clarifying → located → coding → building → preview-ready → merged`）的 FastAPI 单体服务，REST + SSE 全通、状态持久化到 MySQL、真实 git 操作、配额信号量、闲置回收、重试、所有失败路径。

**Architecture:** FastAPI 单体服务，进程内 asyncio 任务编排（不引入独立队列）。所有「栈/工具相关」逻辑收敛到 4 个 `Protocol` adapter 接口背后；Plan 2 定义这些接口 + 共享类型 + 确定性 fake 实现，Plan 3 替换为真实实现。变更请求是一个有限状态机，持久化在 MySQL，由 `Pipeline` 编排器驱动。SSE 把状态变迁推给客户端；`InteractionChannel` 把 adapter 的「问业务员」需求桥接到 SSE + `/answer` 端点。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2.x、PyMySQL + cryptography、Pydantic v2 + pydantic-settings、sse-starlette、pytest + httpx + pytest-asyncio；MySQL 8（复用 demo 的 MySQL 实例，独立 database）；`git` CLI（subprocess）。

**关键设计决策（Plan 2 特有，从设计文档 §4.1/§4.2/§5.x 推导）：**
- adapter 的 4 个 Protocol 接口和共享类型由本计划定义（跨计划契约）；本计划提供确定性 fake 实现，Plan 3 替换为真实实现。
- adapter 方法统一 `async`。
- 「骨架」= 编排机器完整可跑（fake adapter），不含真实 LLM/Docker/栈逻辑。
- 不含 Dockerfile（orchestrator 容器化是 Plan 5 的 docker-in-docker 部署问题）；Plan 2 作为本地 Python 进程运行。
- `retry` 创建新 ChangeRequest（同 RawRequest，`retry_of` 指向旧 id），旧记录保留。
- 新增 `discarded` 终态（设计文档 §5.3 提到、状态图漏画）。

**本计划用到的「需要你提供的清单」项：** 无强依赖。复用 demo 阶段已起的本机 MySQL（容器 `demo-mysql`，`localhost:3307`，root/demopass）。测试用 `orchestrator_test` database。ECS/模型/部署相关清单项留给 Plan 3-5。

---

## 前置约定（每个任务都假定已满足）

- 所有路径以仓库根 `/Users/weizhanhao/doskill` 为基准。orchestrator 代码在 `orchestrator/`，与 `demo/` 同级。
- 后端测试需要一个可连的 MySQL。复用 demo 阶段的 `demo-mysql` 容器（host `localhost:3307`，root 密码 `demopass`）。若该容器不在运行：`docker run -d --name demo-mysql -e MYSQL_ROOT_PASSWORD=demopass -p 3307:3306 mysql:8`，等 ~25s，然后建库（见 Task 7 Step 1）。orchestrator 用 `orchestrator` / `orchestrator_test` 两个 database。
- 测试连接串走环境变量。Python 环境用 `orchestrator/venv/`（Task 1 创建）。
- 提交信息用约定式提交（feat/test/chore/docs）。每个任务结束提交，只提交该任务列出的文件，不提交 `venv/`、`__pycache__/`、`*.egg-info/`。
- `.gitignore` 已在仓库根存在（Plan 1 建立），已覆盖 `__pycache__/`、`*.egg-info/`、`venv/`、`demo/backend/.env` 等。Task 1 会补一条 `orchestrator/.env`。

---

## File Structure

```
orchestrator/
├── pyproject.toml                          # 依赖与项目元数据
├── .env.example                            # 环境变量样例
├── README.md                               # 说明文档（Task 17）
├── src/orchestrator/
│   ├── __init__.py
│   ├── config.py                           # Settings：DATABASE_URL、QUOTA_SIZE、IDLE_TTL_SECONDS 等
│   ├── db.py                               # SQLAlchemy engine/session、Base、get_db
│   ├── models.py                           # ChangeRequest ORM 模型
│   ├── states.py                           # FSM：State 枚举、合法转换表、TERMINAL 集合、校验
│   ├── schemas.py                          # Pydantic API 出入参 schema
│   ├── repository.py                       # ChangeRequestRepository：MySQL CRUD + 状态转换
│   ├── git_manager.py                      # GitManager：切分支/提交/rebase/合并/删分支（真实 git subprocess）
│   ├── events.py                           # EventBus：每请求一个 SSE 事件队列
│   ├── quota.py                            # QuotaManager：并发槽位信号量（带 id 追踪、幂等 release）
│   ├── reaper.py                           # IdleReaper：定时把超时的 preview-ready 标 expired
│   ├── interaction_channel.py              # SSEInteractionChannel：adapter↔SSE/answer 端点桥接
│   ├── pipeline.py                         # Pipeline：FSM 驱动器，串起 clarify→locate→run→build/serve
│   ├── main.py                             # FastAPI app：REST + SSE 端点、lifespan、依赖装配
│   └── adapters/
│       ├── __init__.py
│       ├── types.py                        # 共享 dataclass：RawRequest、RequestBrief、LocateResult…
│       ├── interfaces.py                   # 4 个 Protocol：InteractionSkill/StackAdapter/DevRunnerAdapter/PreviewAdapter + InteractionChannel
│       └── fakes.py                        # FakeInteractionSkill、FakeStackAdapter、FakeDevRunner、FakePreviewAdapter
└── tests/
    ├── conftest.py                         # 测试 DB、临时 git 仓库、TestClient fixtures
    ├── test_config.py
    ├── test_models.py
    ├── test_states.py
    ├── test_adapter_types.py
    ├── test_interfaces.py
    ├── test_fakes.py
    ├── test_repository.py
    ├── test_git_manager.py
    ├── test_events.py
    ├── test_quota.py
    ├── test_interaction_channel.py
    ├── test_pipeline.py
    ├── test_api.py
    ├── test_api_sse.py
    ├── test_reaper.py
    ├── test_lifespan.py
    └── test_integration.py
```

---

## Task 1: 项目骨架与配置

**Files:**
- Create: `orchestrator/pyproject.toml`
- Create: `orchestrator/.env.example`
- Create: `orchestrator/src/orchestrator/__init__.py`
- Create: `orchestrator/src/orchestrator/config.py`
- Test: `orchestrator/tests/test_config.py`
- Modify: `.gitignore`

- [ ] **Step 1: 写 pyproject.toml**

Create `orchestrator/pyproject.toml`:

```toml
[project]
name = "orchestrator"
version = "0.1.0"
description = "AI 原生低代码平台 — Orchestrator 骨架"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0",
    "pymysql>=1.1",
    "cryptography>=42",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "sse-starlette>=2.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: 写 .env.example**

Create `orchestrator/.env.example`:

```
DATABASE_URL=mysql+pymysql://root:demopass@localhost:3307/orchestrator
DEMO_REPO_PATH=/Users/weizhanhao/doskill/demo
QUOTA_SIZE=5
IDLE_TTL_SECONDS=1800
REAPER_INTERVAL_SECONDS=60
```

- [ ] **Step 3: 写包初始化文件**

Create `orchestrator/src/orchestrator/__init__.py` as an empty file (zero bytes).

- [ ] **Step 4: 写失败测试**

Create `orchestrator/tests/test_config.py`:

```python
from orchestrator.config import Settings


def test_settings_reads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@h:3307/db")
    monkeypatch.setenv("QUOTA_SIZE", "9")
    settings = Settings()
    assert settings.database_url == "mysql+pymysql://u:p@h:3307/db"
    assert settings.quota_size == 9


def test_settings_has_defaults(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("QUOTA_SIZE", raising=False)
    monkeypatch.delenv("IDLE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("REAPER_INTERVAL_SECONDS", raising=False)
    settings = Settings()
    assert settings.database_url.startswith("mysql+pymysql://")
    assert settings.quota_size == 5
    assert settings.idle_ttl_seconds == 1800
    assert settings.reaper_interval_seconds == 60
```

- [ ] **Step 5: 运行测试确认失败**

Run: `cd orchestrator && python3 -m venv venv && venv/bin/pip install -e ".[dev]" && venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.config'`

- [ ] **Step 6: 写 config.py**

Create `orchestrator/src/orchestrator/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://root:demopass@localhost:3307/orchestrator"
    demo_repo_path: str = "/Users/weizhanhao/doskill/demo"
    quota_size: int = 5
    idle_ttl_seconds: int = 1800
    reaper_interval_seconds: int = 60


settings = Settings()
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_config.py -v`
Expected: PASS — 2 passed

- [ ] **Step 8: 给 .gitignore 补一条**

Modify `/Users/weizhanhao/doskill/.gitignore` — under the `# Python` section, add a line `orchestrator/.env` immediately after the existing `demo/backend/.env` line. The `# Python` section becomes:

```
# Python
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
venv/
demo/backend/.env
orchestrator/.env
```

(Only that one line is added — do not touch the Node or 环境 sections.)

- [ ] **Step 9: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/pyproject.toml orchestrator/.env.example orchestrator/src/orchestrator/__init__.py orchestrator/src/orchestrator/config.py orchestrator/tests/test_config.py .gitignore
git commit -m "feat: orchestrator 项目骨架与配置"
```

---

## Task 2: DB 连接与 ChangeRequest 模型

**Files:**
- Create: `orchestrator/src/orchestrator/db.py`
- Create: `orchestrator/src/orchestrator/models.py`
- Test: `orchestrator/tests/test_models.py`

- [ ] **Step 1: 写 db.py**

Create `orchestrator/src/orchestrator/db.py`:

```python
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from orchestrator.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: 写失败测试**

Create `orchestrator/tests/test_models.py`:

```python
from orchestrator.models import ChangeRequest


def test_change_request_has_expected_columns():
    cols = set(ChangeRequest.__table__.columns.keys())
    assert cols == {
        "id",
        "url",
        "screenshot_b64",
        "box_coords",
        "viewport",
        "request_text",
        "state",
        "fail_phase",
        "fail_reason",
        "fail_log",
        "branch",
        "preview_url",
        "preview_handle",
        "retry_of",
        "created_at",
        "updated_at",
        "last_activity_at",
    }


def test_change_request_id_is_string_primary_key():
    assert ChangeRequest.__table__.columns["id"].primary_key is True
    assert ChangeRequest.__table__.columns["state"].nullable is False
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.models'`

- [ ] **Step 4: 写 models.py**

Create `orchestrator/src/orchestrator/models.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from orchestrator.db import Base


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    screenshot_b64: Mapped[str] = mapped_column(Text, nullable=False)
    box_coords: Mapped[dict] = mapped_column(JSON, nullable=False)
    viewport: Mapped[dict] = mapped_column(JSON, nullable=False)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    fail_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fail_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preview_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    preview_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_models.py -v`
Expected: PASS — 2 passed

- [ ] **Step 6: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/db.py orchestrator/src/orchestrator/models.py orchestrator/tests/test_models.py
git commit -m "feat: orchestrator DB 连接与 ChangeRequest 模型"
```

---

## Task 3: FSM 状态机

**Files:**
- Create: `orchestrator/src/orchestrator/states.py`
- Test: `orchestrator/tests/test_states.py`

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_states.py`:

```python
from orchestrator.states import State, TERMINAL, is_valid_transition


def test_states_enum_values():
    assert State.CREATED.value == "created"
    assert State.PREVIEW_READY.value == "preview-ready"
    assert State.MERGED.value == "merged"
    assert State.DISCARDED.value == "discarded"


def test_terminal_states():
    assert TERMINAL == {State.MERGED, State.FAILED, State.EXPIRED, State.DISCARDED}


def test_valid_forward_transitions():
    assert is_valid_transition(State.CREATED, State.CLARIFYING)
    assert is_valid_transition(State.CLARIFYING, State.LOCATED)
    assert is_valid_transition(State.LOCATED, State.CODING)
    assert is_valid_transition(State.CODING, State.BUILDING)
    assert is_valid_transition(State.BUILDING, State.PREVIEW_READY)
    assert is_valid_transition(State.PREVIEW_READY, State.MERGED)


def test_any_active_state_can_fail():
    for s in (State.CREATED, State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING):
        assert is_valid_transition(s, State.FAILED)


def test_preview_ready_can_expire_or_discard():
    assert is_valid_transition(State.PREVIEW_READY, State.EXPIRED)
    assert is_valid_transition(State.PREVIEW_READY, State.DISCARDED)


def test_active_states_can_discard():
    assert is_valid_transition(State.CLARIFYING, State.DISCARDED)


def test_invalid_transitions_rejected():
    assert not is_valid_transition(State.MERGED, State.CODING)
    assert not is_valid_transition(State.CREATED, State.PREVIEW_READY)
    assert not is_valid_transition(State.FAILED, State.CLARIFYING)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_states.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.states'`

- [ ] **Step 3: 写 states.py**

Create `orchestrator/src/orchestrator/states.py`:

```python
from enum import Enum


class State(str, Enum):
    CREATED = "created"
    CLARIFYING = "clarifying"
    LOCATED = "located"
    CODING = "coding"
    BUILDING = "building"
    PREVIEW_READY = "preview-ready"
    MERGED = "merged"
    FAILED = "failed"
    EXPIRED = "expired"
    DISCARDED = "discarded"


TERMINAL: set[State] = {State.MERGED, State.FAILED, State.EXPIRED, State.DISCARDED}

# 主流水线推进 + 用户动作（merge/discard）+ 系统动作（expire/fail）的合法转换
_TRANSITIONS: dict[State, set[State]] = {
    State.CREATED: {State.CLARIFYING, State.FAILED, State.DISCARDED},
    State.CLARIFYING: {State.LOCATED, State.FAILED, State.DISCARDED},
    State.LOCATED: {State.CODING, State.FAILED, State.DISCARDED},
    State.CODING: {State.BUILDING, State.FAILED, State.DISCARDED},
    State.BUILDING: {State.PREVIEW_READY, State.FAILED, State.DISCARDED},
    State.PREVIEW_READY: {State.MERGED, State.EXPIRED, State.DISCARDED, State.FAILED},
    State.MERGED: set(),
    State.FAILED: set(),
    State.EXPIRED: set(),
    State.DISCARDED: set(),
}


def is_valid_transition(src: State, dst: State) -> bool:
    return dst in _TRANSITIONS.get(src, set())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_states.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/states.py orchestrator/tests/test_states.py
git commit -m "feat: orchestrator FSM 状态机"
```

---

## Task 4: adapter 契约 —— 共享类型

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/__init__.py`
- Create: `orchestrator/src/orchestrator/adapters/types.py`
- Test: `orchestrator/tests/test_adapter_types.py`

- [ ] **Step 1: 写包初始化文件**

Create `orchestrator/src/orchestrator/adapters/__init__.py` as an empty file (zero bytes).

- [ ] **Step 2: 写失败测试**

Create `orchestrator/tests/test_adapter_types.py`:

```python
from orchestrator.adapters.types import (
    BuildResult,
    DevContext,
    HtmlMockup,
    LocateResult,
    PreviewInstance,
    RawRequest,
    RequestBrief,
    RunResult,
    VariantSelection,
)


def test_raw_request_fields():
    r = RawRequest(
        url="http://x/orders",
        screenshot_b64="abc",
        box_coords={"x": 1, "y": 2, "width": 3, "height": 4},
        viewport={"width": 1280, "height": 800},
        request_text="把按钮改成蓝色",
    )
    assert r.url == "http://x/orders"
    assert r.box_coords["width"] == 3


def test_request_brief_defaults():
    b = RequestBrief(original_text="改个颜色")
    assert b.selected_mockup is None
    assert b.clarifications == []


def test_locate_result_empty_means_not_found():
    miss = LocateResult(entry_files=[], route_path="")
    hit = LocateResult(entry_files=["src/pages/OrderList.tsx"], route_path="/orders")
    assert miss.entry_files == []
    assert hit.entry_files == ["src/pages/OrderList.tsx"]


def test_dev_context_composes_brief_and_locate():
    brief = RequestBrief(original_text="x")
    loc = LocateResult(entry_files=["a.tsx"], route_path="/a")
    ctx = DevContext(
        brief=brief, locate_result=loc, screenshot_b64="img", box_coords={}
    )
    assert ctx.brief is brief
    assert ctx.locate_result is loc


def test_run_build_preview_result_fields():
    assert RunResult(changed=True, commit_sha="abc123", log="ok").changed is True
    assert BuildResult(ok=False, log="boom").ok is False
    pi = PreviewInstance(preview_id="p1", url="http://x:5101", handle="container-1")
    assert pi.url == "http://x:5101"


def test_html_mockup_and_variant_selection():
    m = HtmlMockup(id="v1", title="方案一", html="<div/>")
    assert m.id == "v1"
    assert VariantSelection(selected_id="v1").selected_id == "v1"
    assert VariantSelection(selected_id=None).selected_id is None
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_adapter_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.adapters.types'`

- [ ] **Step 4: 写 types.py**

Create `orchestrator/src/orchestrator/adapters/types.py`:

```python
"""adapter 层的共享数据类型 —— 跨计划契约。Plan 3 的真实 adapter 实现必须遵守这些类型。"""
from dataclasses import dataclass, field


@dataclass
class RawRequest:
    """业务员的原始捕获 —— 扩展 POST 上来的负载。"""

    url: str
    screenshot_b64: str
    box_coords: dict
    viewport: dict
    request_text: str


@dataclass
class HtmlMockup:
    """澄清「重路径」生成的一套轻量 HTML 方案，仅用于传达意图。"""

    id: str
    title: str
    html: str


@dataclass
class VariantSelection:
    """业务员对 HTML 方案的选择；selected_id 为 None 表示全否。"""

    selected_id: str | None


@dataclass
class RequestBrief:
    """澄清产出的业务级需求。"""

    original_text: str
    clarifications: list[dict] = field(default_factory=list)
    selected_mockup: HtmlMockup | None = None


@dataclass
class LocateResult:
    """URL → 源码定位结果；entry_files 为空表示定位失败。"""

    entry_files: list[str]
    route_path: str


@dataclass
class DevContext:
    """组装给 dev runner 的上下文包。"""

    brief: RequestBrief
    locate_result: LocateResult
    screenshot_b64: str
    box_coords: dict


@dataclass
class RunResult:
    """dev runner 跑完的结果；changed 为 False 表示没产出改动。"""

    changed: bool
    commit_sha: str | None
    log: str


@dataclass
class BuildResult:
    """构建结果。"""

    ok: bool
    log: str


@dataclass
class PreviewInstance:
    """预览环境句柄。handle 是 adapter 内部用的标识（容器 id 等）。"""

    preview_id: str
    url: str
    handle: str
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_adapter_types.py -v`
Expected: PASS — 6 passed

- [ ] **Step 6: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/adapters/__init__.py orchestrator/src/orchestrator/adapters/types.py orchestrator/tests/test_adapter_types.py
git commit -m "feat: orchestrator adapter 共享类型契约"
```

---

## Task 5: adapter 契约 —— Protocol 接口

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/interfaces.py`
- Test: `orchestrator/tests/test_interfaces.py`

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_interfaces.py`:

```python
import inspect

from orchestrator.adapters.interfaces import (
    DevRunnerAdapter,
    InteractionChannel,
    InteractionSkill,
    PreviewAdapter,
    StackAdapter,
)


def test_interaction_channel_methods_are_async():
    assert inspect.iscoroutinefunction(InteractionChannel.ask)
    assert inspect.iscoroutinefunction(InteractionChannel.present_variants)


def test_interaction_skill_clarify_is_async():
    assert inspect.iscoroutinefunction(InteractionSkill.clarify)


def test_stack_adapter_methods_are_async():
    assert inspect.iscoroutinefunction(StackAdapter.locate)
    assert inspect.iscoroutinefunction(StackAdapter.context_pack)
    assert inspect.iscoroutinefunction(StackAdapter.build)


def test_dev_runner_run_is_async():
    assert inspect.iscoroutinefunction(DevRunnerAdapter.run)


def test_preview_adapter_methods_are_async():
    assert inspect.iscoroutinefunction(PreviewAdapter.serve)
    assert inspect.iscoroutinefunction(PreviewAdapter.teardown)


def test_interfaces_are_runtime_checkable_protocols():
    # 一个满足结构的对象应被 isinstance 认可
    class _Stub:
        async def run(self, repo_path, branch, ctx):
            ...

    assert isinstance(_Stub(), DevRunnerAdapter)

    class _NotStub:
        pass

    assert not isinstance(_NotStub(), DevRunnerAdapter)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_interfaces.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.adapters.interfaces'`

- [ ] **Step 3: 写 interfaces.py**

Create `orchestrator/src/orchestrator/adapters/interfaces.py`:

```python
"""4 个 adapter 的 Protocol 接口 —— 跨计划契约。所有方法 async。

Plan 2 提供 fake 实现（fakes.py），Plan 3 提供真实实现。Orchestrator 主体只依赖这些接口。
"""
from typing import Protocol, runtime_checkable

from orchestrator.adapters.types import (
    BuildResult,
    DevContext,
    HtmlMockup,
    LocateResult,
    PreviewInstance,
    RawRequest,
    RequestBrief,
    RunResult,
    VariantSelection,
)


@runtime_checkable
class InteractionChannel(Protocol):
    """adapter 用它来「问业务员」；实现负责把问题经 SSE 推出去、等 /answer 端点回填。"""

    async def ask(self, question: str, options: list[str] | None) -> str: ...

    async def present_variants(
        self, variants: list[HtmlMockup]
    ) -> VariantSelection: ...


@runtime_checkable
class InteractionSkill(Protocol):
    """交互层：只问业务、不碰技术，产出业务级 RequestBrief。"""

    async def clarify(
        self, raw: RawRequest, channel: InteractionChannel
    ) -> RequestBrief: ...


@runtime_checkable
class StackAdapter(Protocol):
    """栈层：URL→源码定位、上下文组装、构建。"""

    async def locate(self, url: str) -> LocateResult: ...

    async def context_pack(
        self, locate_result: LocateResult, raw: RawRequest, brief: RequestBrief
    ) -> DevContext: ...

    async def build(self, repo_path: str, branch: str) -> BuildResult: ...


@runtime_checkable
class DevRunnerAdapter(Protocol):
    """开发层：业务 brief → 真实代码改动并 commit。"""

    async def run(
        self, repo_path: str, branch: str, ctx: DevContext
    ) -> RunResult: ...


@runtime_checkable
class PreviewAdapter(Protocol):
    """预览层：分支 → 隔离预览环境。"""

    async def serve(self, repo_path: str, branch: str) -> PreviewInstance: ...

    async def teardown(self, instance: PreviewInstance) -> None: ...
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_interfaces.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/adapters/interfaces.py orchestrator/tests/test_interfaces.py
git commit -m "feat: orchestrator adapter Protocol 接口"
```

---

## Task 6: fake adapters

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/fakes.py`
- Test: `orchestrator/tests/test_fakes.py`

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_fakes.py`:

```python
import pytest

from orchestrator.adapters.fakes import (
    FakeDevRunner,
    FakeInteractionSkill,
    FakePreviewAdapter,
    FakeStackAdapter,
)
from orchestrator.adapters.types import DevContext, LocateResult, RawRequest, RequestBrief


def _raw() -> RawRequest:
    return RawRequest(
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={},
        viewport={},
        request_text="把保存按钮改成蓝色",
    )


class _RecordingChannel:
    """记录 ask/present_variants 调用的测试用 channel；按预设答案回复。"""

    def __init__(self, answers: list[str], selection_id: str | None = None):
        self._answers = list(answers)
        self._selection_id = selection_id
        self.asked: list[str] = []

    async def ask(self, question, options):
        self.asked.append(question)
        return self._answers.pop(0)

    async def present_variants(self, variants):
        from orchestrator.adapters.types import VariantSelection

        return VariantSelection(selected_id=self._selection_id)


async def test_fake_interaction_skill_skip_path():
    skill = FakeInteractionSkill(question_count=0)
    channel = _RecordingChannel(answers=[])
    brief = await skill.clarify(_raw(), channel)
    assert isinstance(brief, RequestBrief)
    assert brief.original_text == "把保存按钮改成蓝色"
    assert channel.asked == []


async def test_fake_interaction_skill_scripted_questions():
    skill = FakeInteractionSkill(question_count=2)
    channel = _RecordingChannel(answers=["答案1", "答案2"])
    brief = await skill.clarify(_raw(), channel)
    assert len(channel.asked) == 2
    assert len(brief.clarifications) == 2
    assert brief.clarifications[0]["answer"] == "答案1"


async def test_fake_stack_adapter_locate_hit_and_miss():
    adapter = FakeStackAdapter()
    hit = await adapter.locate("http://x/orders")
    assert hit.entry_files  # 默认命中
    miss_adapter = FakeStackAdapter(locate_succeeds=False)
    miss = await miss_adapter.locate("http://x/orders")
    assert miss.entry_files == []


async def test_fake_stack_adapter_build_outcome_configurable():
    assert (await FakeStackAdapter().build("/repo", "cr/1")).ok is True
    assert (await FakeStackAdapter(build_succeeds=False).build("/repo", "cr/1")).ok is False


async def test_fake_dev_runner_outcomes():
    ctx = DevContext(
        brief=RequestBrief(original_text="x"),
        locate_result=LocateResult(entry_files=["a"], route_path="/a"),
        screenshot_b64="img",
        box_coords={},
    )
    ok = await FakeDevRunner().run("/repo", "cr/1", ctx)
    assert ok.changed is True and ok.commit_sha is not None
    no_change = await FakeDevRunner(produces_changes=False).run("/repo", "cr/1", ctx)
    assert no_change.changed is False
    with pytest.raises(RuntimeError):
        await FakeDevRunner(raises=True).run("/repo", "cr/1", ctx)


async def test_fake_preview_adapter_serve_and_teardown():
    adapter = FakePreviewAdapter()
    inst = await adapter.serve("/repo", "cr/1")
    assert inst.url.startswith("http://")
    assert inst.handle in adapter.live_handles
    await adapter.teardown(inst)
    assert inst.handle not in adapter.live_handles
    with pytest.raises(RuntimeError):
        await FakePreviewAdapter(serve_succeeds=False).serve("/repo", "cr/1")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_fakes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.adapters.fakes'`

- [ ] **Step 3: 写 fakes.py**

Create `orchestrator/src/orchestrator/adapters/fakes.py`:

```python
"""确定性 fake adapter 实现 —— 让 Orchestrator 编排机器能在测试里跑通完整 FSM。

Plan 3 用真实实现替换它们；Orchestrator 主体不变。每个 fake 的「行为开关」让测试能精确
触发成功/失败/无产出等路径。
"""
import uuid

from orchestrator.adapters.interfaces import InteractionChannel
from orchestrator.adapters.types import (
    BuildResult,
    DevContext,
    LocateResult,
    PreviewInstance,
    RawRequest,
    RequestBrief,
    RunResult,
)


class FakeInteractionSkill:
    """按 question_count 走脚本化澄清；0 表示直接跳过。"""

    def __init__(self, question_count: int = 0):
        self._question_count = question_count

    async def clarify(
        self, raw: RawRequest, channel: InteractionChannel
    ) -> RequestBrief:
        clarifications: list[dict] = []
        for i in range(self._question_count):
            q = f"澄清问题 {i + 1}：你想要的业务效果是？"
            answer = await channel.ask(q, None)
            clarifications.append({"question": q, "answer": answer})
        return RequestBrief(
            original_text=raw.request_text, clarifications=clarifications
        )


class FakeStackAdapter:
    def __init__(self, locate_succeeds: bool = True, build_succeeds: bool = True):
        self._locate_succeeds = locate_succeeds
        self._build_succeeds = build_succeeds

    async def locate(self, url: str) -> LocateResult:
        if not self._locate_succeeds:
            return LocateResult(entry_files=[], route_path="")
        return LocateResult(
            entry_files=["src/pages/OrderList.tsx"], route_path="/orders"
        )

    async def context_pack(
        self, locate_result: LocateResult, raw: RawRequest, brief: RequestBrief
    ) -> DevContext:
        return DevContext(
            brief=brief,
            locate_result=locate_result,
            screenshot_b64=raw.screenshot_b64,
            box_coords=raw.box_coords,
        )

    async def build(self, repo_path: str, branch: str) -> BuildResult:
        if not self._build_succeeds:
            return BuildResult(ok=False, log="fake build failure")
        return BuildResult(ok=True, log="fake build ok")


class FakeDevRunner:
    def __init__(self, produces_changes: bool = True, raises: bool = False):
        self._produces_changes = produces_changes
        self._raises = raises

    async def run(
        self, repo_path: str, branch: str, ctx: DevContext
    ) -> RunResult:
        if self._raises:
            raise RuntimeError("fake dev runner crash")
        if not self._produces_changes:
            return RunResult(changed=False, commit_sha=None, log="fake: no changes")
        return RunResult(
            changed=True, commit_sha=uuid.uuid4().hex[:12], log="fake: changed 1 file"
        )


class FakePreviewAdapter:
    def __init__(self, serve_succeeds: bool = True):
        self._serve_succeeds = serve_succeeds
        self.live_handles: set[str] = set()
        self._port = 5100

    async def serve(self, repo_path: str, branch: str) -> PreviewInstance:
        if not self._serve_succeeds:
            raise RuntimeError("fake preview serve failure")
        self._port += 1
        handle = f"fake-container-{uuid.uuid4().hex[:8]}"
        self.live_handles.add(handle)
        return PreviewInstance(
            preview_id=uuid.uuid4().hex[:12],
            url=f"http://localhost:{self._port}",
            handle=handle,
        )

    async def teardown(self, instance: PreviewInstance) -> None:
        self.live_handles.discard(instance.handle)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_fakes.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/adapters/fakes.py orchestrator/tests/test_fakes.py
git commit -m "feat: orchestrator fake adapter 实现"
```

---

## Task 7: ChangeRequest 仓储 + 测试夹具

**Files:**
- Create: `orchestrator/src/orchestrator/repository.py`
- Create: `orchestrator/tests/conftest.py`
- Test: `orchestrator/tests/test_repository.py`

- [ ] **Step 1: 起测试 MySQL 并建库**

Run (idempotent — container may already exist from demo work):
```
docker start demo-mysql 2>/dev/null || docker run -d --name demo-mysql -e MYSQL_ROOT_PASSWORD=demopass -p 3307:3306 mysql:8
sleep 25
docker exec demo-mysql mysql -uroot -pdemopass -e "CREATE DATABASE IF NOT EXISTS orchestrator; CREATE DATABASE IF NOT EXISTS orchestrator_test;"
```
Expected: no errors; databases `orchestrator` and `orchestrator_test` exist.

- [ ] **Step 2: 写 conftest.py**

Create `orchestrator/tests/conftest.py`:

```python
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "mysql+pymysql://root:demopass@localhost:3307/orchestrator_test",
)


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    from orchestrator.db import Base
    import orchestrator.models  # noqa: F401  保证模型被注册

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(test_engine):
    session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)()
    from orchestrator.models import ChangeRequest

    session.query(ChangeRequest).delete()
    session.commit()
    yield session
    session.close()
```

- [ ] **Step 3: 写失败测试**

Create `orchestrator/tests/test_repository.py`:

```python
import pytest

from orchestrator.adapters.types import RawRequest
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


def _raw(text="改个颜色") -> RawRequest:
    return RawRequest(
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={"x": 1},
        viewport={"width": 1280},
        request_text=text,
    )


def test_create_returns_record_in_created_state(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    assert cr.id
    assert cr.state == State.CREATED.value
    assert cr.request_text == "改个颜色"
    assert cr.branch is None


def test_get_returns_persisted_record(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw("找回它"))
    fetched = repo.get(cr.id)
    assert fetched is not None
    assert fetched.request_text == "找回它"


def test_get_missing_returns_none(db_session):
    repo = ChangeRequestRepository(db_session)
    assert repo.get("nonexistent") is None


def test_transition_state_valid(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    repo.transition(cr.id, State.CLARIFYING)
    assert repo.get(cr.id).state == State.CLARIFYING.value


def test_transition_state_invalid_raises(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    with pytest.raises(ValueError):
        repo.transition(cr.id, State.PREVIEW_READY)  # created → preview-ready 非法


def test_mark_failed_records_phase_reason_log(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    repo.transition(cr.id, State.CLARIFYING)
    repo.mark_failed(cr.id, phase="coding", reason="crash", log="traceback...")
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "coding"
    assert fetched.fail_reason == "crash"
    assert fetched.fail_log == "traceback..."


def test_set_branch_and_preview(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    repo.set_branch(cr.id, "cr/abc")
    repo.set_preview(cr.id, url="http://x:5101", handle="container-1")
    fetched = repo.get(cr.id)
    assert fetched.branch == "cr/abc"
    assert fetched.preview_url == "http://x:5101"
    assert fetched.preview_handle == "container-1"


def test_touch_activity_updates_last_activity(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    before = repo.get(cr.id).last_activity_at
    repo.touch_activity(cr.id)
    assert repo.get(cr.id).last_activity_at >= before


def test_list_non_terminal_and_stale(db_session):
    from datetime import datetime, timedelta

    repo = ChangeRequestRepository(db_session)
    active = repo.create(_raw("active"))
    repo.transition(active.id, State.CLARIFYING)
    done = repo.create(_raw("done"))
    repo.transition(done.id, State.CLARIFYING)
    repo.transition(done.id, State.DISCARDED)
    stale = repo.create(_raw("stale"))
    for s in (State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING, State.PREVIEW_READY):
        repo.transition(stale.id, s)
    # 把 stale 的 last_activity 拨到很久以前
    obj = repo.get(stale.id)
    obj.last_activity_at = datetime.utcnow() - timedelta(hours=2)
    db_session.commit()

    non_terminal_ids = {c.id for c in repo.list_non_terminal()}
    assert active.id in non_terminal_ids
    assert stale.id in non_terminal_ids
    assert done.id not in non_terminal_ids

    stale_ids = {c.id for c in repo.list_stale_previews(older_than_seconds=3600)}
    assert stale.id in stale_ids
    assert active.id not in stale_ids
```

- [ ] **Step 4: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.repository'`

- [ ] **Step 5: 写 repository.py**

Create `orchestrator/src/orchestrator/repository.py`:

```python
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

    def create(self, raw: RawRequest, retry_of: str | None = None) -> ChangeRequest:
        cr = ChangeRequest(
            id=uuid.uuid4().hex,
            url=raw.url,
            screenshot_b64=raw.screenshot_b64,
            box_coords=raw.box_coords,
            viewport=raw.viewport,
            request_text=raw.request_text,
            state=State.CREATED.value,
            retry_of=retry_of,
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
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_repository.py tests/test_config.py tests/test_models.py tests/test_states.py tests/test_adapter_types.py tests/test_interfaces.py tests/test_fakes.py -v`
Expected: PASS — repository(9) + config(2) + models(2) + states(7) + adapter_types(6) + interfaces(6) + fakes(6) = 38 passed

- [ ] **Step 7: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/repository.py orchestrator/tests/conftest.py orchestrator/tests/test_repository.py
git commit -m "feat: orchestrator ChangeRequest 仓储与测试夹具"
```

---

## Task 8: Git 管理器

**Files:**
- Create: `orchestrator/src/orchestrator/git_manager.py`
- Test: `orchestrator/tests/test_git_manager.py`

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_git_manager.py`:

```python
import subprocess
from pathlib import Path

import pytest

from orchestrator.git_manager import GitConflictError, GitManager


@pytest.fixture
def temp_repo(tmp_path) -> Path:
    """一个有 main 分支 + 一个初始提交的临时 git 仓库。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "test@test")
    run("config", "user.name", "test")
    (repo / "file.txt").write_text("line1\n")
    run("add", "file.txt")
    run("commit", "-m", "init")
    return repo


def test_create_branch_from_main(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    branches = subprocess.run(
        ["git", "branch"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "cr/abc" in branches


def test_commit_all_on_branch(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    (temp_repo / "file.txt").write_text("line1\nline2\n")
    sha = gm.commit_all("cr/abc", "cr: change")
    assert sha
    log = subprocess.run(
        ["git", "log", "--oneline", "cr/abc"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "cr: change" in log


def test_has_changes(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    assert gm.has_changes("cr/abc") is False
    (temp_repo / "file.txt").write_text("changed\n")
    assert gm.has_changes("cr/abc") is True


def test_merge_clean(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    (temp_repo / "new.txt").write_text("brand new\n")
    gm.commit_all("cr/abc", "cr: add new file")
    gm.merge_to_main("cr/abc")
    main_files = subprocess.run(
        ["git", "ls-tree", "--name-only", "main"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "new.txt" in main_files


def test_merge_conflict_raises(temp_repo):
    gm = GitManager(str(temp_repo))
    # 分支改 file.txt
    gm.create_branch("cr/abc")
    (temp_repo / "file.txt").write_text("branch version\n")
    gm.commit_all("cr/abc", "cr: branch edit")
    # main 也改 file.txt（制造冲突）
    run = lambda *a: subprocess.run(["git", *a], cwd=temp_repo, check=True, capture_output=True)
    run("checkout", "main")
    (temp_repo / "file.txt").write_text("main version\n")
    run("add", "file.txt")
    run("commit", "-m", "main edit")
    with pytest.raises(GitConflictError):
        gm.merge_to_main("cr/abc")
    # 冲突后仓库应回到干净的 main（不留半合并状态）
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert status.strip() == ""


def test_delete_branch(temp_repo):
    gm = GitManager(str(temp_repo))
    gm.create_branch("cr/abc")
    gm.delete_branch("cr/abc")
    branches = subprocess.run(
        ["git", "branch"], cwd=temp_repo, capture_output=True, text=True
    ).stdout
    assert "cr/abc" not in branches
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_git_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.git_manager'`

- [ ] **Step 3: 写 git_manager.py**

Create `orchestrator/src/orchestrator/git_manager.py`:

```python
"""GitManager —— 对目标仓库做真实 git 操作。所有方法同步（subprocess）；
Pipeline 在 async 上下文里用 asyncio.to_thread 调用它们。
"""
import subprocess


class GitConflictError(Exception):
    """rebase/merge 出现冲突。"""


class GitManager:
    def __init__(self, repo_path: str):
        self._repo = repo_path

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self._repo,
            check=check,
            capture_output=True,
            text=True,
        )

    def create_branch(self, branch: str) -> None:
        """从 main 切一个新分支并切过去。"""
        self._git("checkout", "main")
        self._git("checkout", "-b", branch)

    def has_changes(self, branch: str) -> bool:
        """branch 的工作树相对 HEAD 是否有未提交改动。"""
        self._git("checkout", branch)
        result = self._git("status", "--porcelain")
        return bool(result.stdout.strip())

    def commit_all(self, branch: str, message: str) -> str:
        """在 branch 上 add -A 并提交，返回 commit SHA。"""
        self._git("checkout", branch)
        self._git("add", "-A")
        self._git("commit", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def merge_to_main(self, branch: str) -> None:
        """先把 branch rebase 到最新 main，再 fast-forward 合并进 main。
        rebase 或 merge 冲突 → 回滚到干净状态并抛 GitConflictError。
        """
        self._git("checkout", branch)
        rebase = self._git("rebase", "main", check=False)
        if rebase.returncode != 0:
            self._git("rebase", "--abort", check=False)
            self._git("checkout", "main")
            raise GitConflictError(f"rebase conflict: {rebase.stdout}{rebase.stderr}")
        self._git("checkout", "main")
        merge = self._git("merge", "--ff-only", branch, check=False)
        if merge.returncode != 0:
            raise GitConflictError(f"merge failed: {merge.stdout}{merge.stderr}")

    def delete_branch(self, branch: str) -> None:
        """删除分支（强制）。先确保不在该分支上。"""
        self._git("checkout", "main")
        self._git("branch", "-D", branch, check=False)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_git_manager.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/git_manager.py orchestrator/tests/test_git_manager.py
git commit -m "feat: orchestrator Git 管理器"
```

---

## Task 9: SSE 事件总线

**Files:**
- Create: `orchestrator/src/orchestrator/events.py`
- Test: `orchestrator/tests/test_events.py`

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_events.py`:

```python
import asyncio

import pytest

from orchestrator.events import Event, EventBus


async def test_publish_then_subscribe_receives_event():
    bus = EventBus()
    await bus.publish("req-1", Event(type="status", data={"state": "clarifying"}))
    sub = bus.subscribe("req-1")
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.type == "status"
    assert evt.data["state"] == "clarifying"


async def test_subscribe_receives_live_events():
    bus = EventBus()
    sub = bus.subscribe("req-2")

    async def producer():
        await asyncio.sleep(0.01)
        await bus.publish("req-2", Event(type="status", data={"state": "coding"}))

    asyncio.create_task(producer())
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.data["state"] == "coding"


async def test_events_are_isolated_per_request():
    bus = EventBus()
    await bus.publish("req-a", Event(type="status", data={"x": 1}))
    await bus.publish("req-b", Event(type="status", data={"x": 2}))
    sub_b = bus.subscribe("req-b")
    evt = await asyncio.wait_for(sub_b.__anext__(), timeout=1)
    assert evt.data["x"] == 2


async def test_replayed_history_not_duplicated_then_live_event_arrives():
    bus = EventBus()
    await bus.publish("req-3", Event(type="status", data={"n": 1}))
    await bus.publish("req-3", Event(type="status", data={"n": 2}))
    sub = bus.subscribe("req-3")
    # 回放两个历史事件
    e1 = await asyncio.wait_for(sub.__anext__(), timeout=1)
    e2 = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert [e1.data["n"], e2.data["n"]] == [1, 2]
    # 再来一个新事件，应只收到这一个（历史不重复）
    await bus.publish("req-3", Event(type="status", data={"n": 3}))
    e3 = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert e3.data["n"] == 3


async def test_close_ends_subscription():
    bus = EventBus()
    sub = bus.subscribe("req-4")
    await bus.publish("req-4", Event(type="status", data={}))
    await sub.__anext__()  # consume the one event
    bus.close("req-4")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(sub.__anext__(), timeout=1)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.events'`

- [ ] **Step 3: 写 events.py**

Create `orchestrator/src/orchestrator/events.py`:

```python
"""EventBus —— 每个变更请求一个事件队列，供 SSE 端点订阅、Pipeline 发布。

历史事件保留在 buffer 里：晚订阅的客户端（或 SSE 重连）也能拿到此前的状态变迁。
publish 把事件同时写 buffer（供回放）和 queue（供实时）；subscribe 先回放 buffer，
再消费 queue —— 跳过 queue 里与已回放历史等量的旧事件以避免重复。
"""
import asyncio
from dataclasses import dataclass, field


@dataclass
class Event:
    type: str  # "status" | "question" | "variants"
    data: dict


_SENTINEL = object()


@dataclass
class _Channel:
    buffer: list[Event] = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    closed: bool = False


class EventBus:
    def __init__(self) -> None:
        self._channels: dict[str, _Channel] = {}

    def _channel(self, request_id: str) -> _Channel:
        if request_id not in self._channels:
            self._channels[request_id] = _Channel()
        return self._channels[request_id]

    async def publish(self, request_id: str, event: Event) -> None:
        ch = self._channel(request_id)
        ch.buffer.append(event)
        await ch.queue.put(event)

    def close(self, request_id: str) -> None:
        ch = self._channel(request_id)
        ch.closed = True
        ch.queue.put_nowait(_SENTINEL)

    async def subscribe(self, request_id: str):
        """异步生成器：先回放 buffer 里的历史事件，再实时产出后续新事件。"""
        ch = self._channel(request_id)
        replayed = len(ch.buffer)
        for evt in list(ch.buffer):
            yield evt
        # queue 里前 `replayed` 个是已回放过的历史事件，丢弃
        skipped = 0
        while True:
            item = await ch.queue.get()
            if item is _SENTINEL:
                return
            if skipped < replayed:
                skipped += 1
                continue
            yield item
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_events.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/events.py orchestrator/tests/test_events.py
git commit -m "feat: orchestrator SSE 事件总线"
```

---

## Task 10: 配额信号量

**Files:**
- Create: `orchestrator/src/orchestrator/quota.py`
- Test: `orchestrator/tests/test_quota.py`

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_quota.py`:

```python
import asyncio

from orchestrator.quota import QuotaManager


async def test_acquire_within_capacity_does_not_block():
    q = QuotaManager(capacity=2)
    await asyncio.wait_for(q.acquire("r1"), timeout=1)
    await asyncio.wait_for(q.acquire("r2"), timeout=1)
    assert q.in_use() == 2


async def test_acquire_beyond_capacity_blocks_until_release():
    q = QuotaManager(capacity=1)
    await q.acquire("r1")
    waiting = asyncio.create_task(q.acquire("r2"))
    await asyncio.sleep(0.02)
    assert not waiting.done()  # r2 还在排队
    q.release("r1")
    await asyncio.wait_for(waiting, timeout=1)
    assert q.in_use() == 1  # r1 已放，r2 已占


async def test_release_is_idempotent():
    q = QuotaManager(capacity=1)
    await q.acquire("r1")
    q.release("r1")
    q.release("r1")  # 再次 release 不报错、不多放槽
    q.release("unknown")  # 释放未知 id 也安全
    assert q.in_use() == 0


async def test_waiting_count_reflects_queue():
    q = QuotaManager(capacity=1)
    await q.acquire("r1")
    t2 = asyncio.create_task(q.acquire("r2"))
    t3 = asyncio.create_task(q.acquire("r3"))
    await asyncio.sleep(0.02)
    assert q.waiting() == 2
    q.release("r1")
    q.release("r2")
    await asyncio.wait_for(asyncio.gather(t2, t3), timeout=1)
    assert q.waiting() == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_quota.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.quota'`

- [ ] **Step 3: 写 quota.py**

Create `orchestrator/src/orchestrator/quota.py`:

```python
"""QuotaManager —— 限制同时在飞的变更请求数（设计文档把槽位对应到「容器」）。

请求离开 `created` 前 acquire 一个槽位；进入任一终态时 release。release 幂等。
"""
import asyncio


class QuotaManager:
    def __init__(self, capacity: int):
        self._capacity = capacity
        self._held: set[str] = set()
        self._waiters: list[asyncio.Future] = []

    def in_use(self) -> int:
        return len(self._held)

    def waiting(self) -> int:
        return len(self._waiters)

    async def acquire(self, request_id: str) -> None:
        if request_id in self._held:
            return
        if len(self._held) < self._capacity:
            self._held.add(request_id)
            return
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._waiters.append(fut)
        await fut
        self._held.add(request_id)

    def release(self, request_id: str) -> None:
        if request_id not in self._held:
            return
        self._held.discard(request_id)
        if self._waiters:
            fut = self._waiters.pop(0)
            if not fut.done():
                fut.set_result(None)
```

> 说明：`release` 唤醒一个等待者时，等待者醒来后立即 `self._held.add` —— 此刻 `_held` 已因 release 腾出一格，所以总数守恒。`acquire` 对已持有的 id 幂等返回。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_quota.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/quota.py orchestrator/tests/test_quota.py
git commit -m "feat: orchestrator 配额信号量"
```

---

## Task 11: SSE 交互通道

**Files:**
- Create: `orchestrator/src/orchestrator/interaction_channel.py`
- Test: `orchestrator/tests/test_interaction_channel.py`

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_interaction_channel.py`:

```python
import asyncio

from orchestrator.adapters.types import HtmlMockup
from orchestrator.events import EventBus
from orchestrator.interaction_channel import SSEInteractionChannel


async def test_ask_publishes_question_event_and_waits_for_answer():
    bus = EventBus()
    channel = SSEInteractionChannel(request_id="r1", event_bus=bus)
    sub = bus.subscribe("r1")

    ask_task = asyncio.create_task(channel.ask("你想要什么效果？", None))
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.type == "question"
    assert evt.data["question"] == "你想要什么效果？"
    qid = evt.data["question_id"]

    assert not ask_task.done()  # 还在等回答
    channel.submit_answer(qid, "更显眼")
    answer = await asyncio.wait_for(ask_task, timeout=1)
    assert answer == "更显眼"


async def test_present_variants_publishes_event_and_waits_for_selection():
    bus = EventBus()
    channel = SSEInteractionChannel(request_id="r2", event_bus=bus)
    sub = bus.subscribe("r2")

    variants = [HtmlMockup(id="v1", title="方案一", html="<a/>")]
    task = asyncio.create_task(channel.present_variants(variants))
    evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert evt.type == "variants"
    assert evt.data["variants"][0]["id"] == "v1"
    qid = evt.data["question_id"]

    channel.submit_answer(qid, "v1")
    selection = await asyncio.wait_for(task, timeout=1)
    assert selection.selected_id == "v1"


async def test_submit_answer_for_unknown_question_is_ignored():
    bus = EventBus()
    channel = SSEInteractionChannel(request_id="r3", event_bus=bus)
    # 不抛异常即可
    channel.submit_answer("no-such-question", "whatever")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_interaction_channel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.interaction_channel'`

- [ ] **Step 3: 写 interaction_channel.py**

Create `orchestrator/src/orchestrator/interaction_channel.py`:

```python
"""SSEInteractionChannel —— 把 InteractionSkill 的「问业务员」桥接到 SSE + /answer 端点。

ask/present_variants 发一个 question/variants 事件、生成一个 question_id、挂起等待；
REST 的 /answer 端点收到回答后调 submit_answer(question_id, answer) 唤醒。
"""
import asyncio
import uuid

from orchestrator.adapters.types import HtmlMockup, VariantSelection
from orchestrator.events import Event, EventBus


class SSEInteractionChannel:
    def __init__(self, request_id: str, event_bus: EventBus):
        self._request_id = request_id
        self._bus = event_bus
        self._pending: dict[str, asyncio.Future] = {}

    async def ask(self, question: str, options: list[str] | None) -> str:
        question_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[question_id] = fut
        await self._bus.publish(
            self._request_id,
            Event(
                type="question",
                data={
                    "question_id": question_id,
                    "question": question,
                    "options": options,
                },
            ),
        )
        return await fut

    async def present_variants(
        self, variants: list[HtmlMockup]
    ) -> VariantSelection:
        question_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[question_id] = fut
        await self._bus.publish(
            self._request_id,
            Event(
                type="variants",
                data={
                    "question_id": question_id,
                    "variants": [
                        {"id": v.id, "title": v.title, "html": v.html}
                        for v in variants
                    ],
                },
            ),
        )
        selected_id = await fut
        return VariantSelection(selected_id=selected_id or None)

    def submit_answer(self, question_id: str, answer: str) -> None:
        fut = self._pending.pop(question_id, None)
        if fut is not None and not fut.done():
            fut.set_result(answer)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_interaction_channel.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/interaction_channel.py orchestrator/tests/test_interaction_channel.py
git commit -m "feat: orchestrator SSE 交互通道"
```

---

## Task 12: 流水线编排器

**Files:**
- Create: `orchestrator/src/orchestrator/pipeline.py`
- Test: `orchestrator/tests/test_pipeline.py`

> **关键实现约束（给执行者）：** Pipeline 在 `coding` 阶段**只** `create_branch`，然后把「改代码 + commit」整个委托给 `dev_runner.run`（设计文档 §4.2：「在分支上改代码并 commit」）。Pipeline **不**自己调用 `git_manager.commit_all`。`commit_all` 仅供 GitManager 的真实使用者（Plan 3 的真实 DevRunner）或测试调用。

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_pipeline.py`:

```python
import asyncio
import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.fakes import (
    FakeDevRunner,
    FakeInteractionSkill,
    FakePreviewAdapter,
    FakeStackAdapter,
)
from orchestrator.adapters.types import RawRequest
from orchestrator.events import EventBus
from orchestrator.git_manager import GitManager
from orchestrator.pipeline import Pipeline
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


@pytest.fixture
def temp_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "test@test")
    run("config", "user.name", "test")
    (repo / "file.txt").write_text("v1\n")
    run("add", "file.txt")
    run("commit", "-m", "init")
    return repo


def _raw() -> RawRequest:
    return RawRequest(
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={},
        viewport={},
        request_text="把保存按钮改成蓝色",
    )


def _make_pipeline(
    temp_repo,
    db_session,
    *,
    interaction=None,
    stack=None,
    dev=None,
    preview=None,
    quota=None,
):
    return Pipeline(
        repo_path=str(temp_repo),
        repository=ChangeRequestRepository(db_session),
        git_manager=GitManager(str(temp_repo)),
        event_bus=EventBus(),
        quota=quota or QuotaManager(capacity=5),
        interaction_skill=interaction or FakeInteractionSkill(question_count=0),
        stack_adapter=stack or FakeStackAdapter(),
        dev_runner=dev or FakeDevRunner(),
        preview_adapter=preview or FakePreviewAdapter(),
    )


async def test_happy_path_reaches_preview_ready(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(temp_repo, db_session)
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.PREVIEW_READY.value
    assert fetched.branch == f"cr/{cr.id}"
    assert fetched.preview_url is not None
    assert fetched.preview_handle is not None


async def test_locate_failure_fails_at_located(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, stack=FakeStackAdapter(locate_succeeds=False)
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "located"


async def test_dev_runner_crash_fails_at_coding(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(temp_repo, db_session, dev=FakeDevRunner(raises=True))
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "coding"


async def test_dev_runner_no_changes_fails_at_coding(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, dev=FakeDevRunner(produces_changes=False)
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "coding"
    assert fetched.fail_reason == "no-changes"


async def test_build_failure_fails_at_building(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, stack=FakeStackAdapter(build_succeeds=False)
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "building"


async def test_preview_serve_failure_fails_at_building(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, preview=FakePreviewAdapter(serve_succeeds=False)
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.FAILED.value
    assert fetched.fail_phase == "building"


async def test_scripted_clarification_runs_interactive_dance(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    pipeline = _make_pipeline(
        temp_repo, db_session, interaction=FakeInteractionSkill(question_count=2)
    )
    # 在后台跑 pipeline；它会在 clarifying 阶段挂起等回答
    task = asyncio.create_task(pipeline.run(cr.id))
    await asyncio.sleep(0.05)
    assert repo.get(cr.id).state == State.CLARIFYING.value
    # 回答两个澄清问题
    channel = pipeline.channel_for(cr.id)
    sub = pipeline.event_bus.subscribe(cr.id)
    answered = 0
    while answered < 2:
        evt = await asyncio.wait_for(sub.__anext__(), timeout=1)
        if evt.type == "question":
            channel.submit_answer(evt.data["question_id"], "我的回答")
            answered += 1
    await asyncio.wait_for(task, timeout=2)
    assert repo.get(cr.id).state == State.PREVIEW_READY.value


async def test_quota_slot_held_at_preview_ready_released_on_failure(temp_repo, db_session):
    repo = ChangeRequestRepository(db_session)
    quota = QuotaManager(capacity=5)
    # happy path 到 preview-ready（非终态）—— 槽位仍被占用
    cr = repo.create(_raw())
    await _make_pipeline(temp_repo, db_session, quota=quota).run(cr.id)
    assert quota.in_use() == 1
    # 失败路径会释放槽位
    cr2 = repo.create(_raw())
    await _make_pipeline(
        temp_repo, db_session, quota=quota, dev=FakeDevRunner(raises=True)
    ).run(cr2.id)
    assert quota.in_use() == 1  # cr2 失败已释放，只剩 cr1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.pipeline'`

- [ ] **Step 3: 写 pipeline.py**

Create `orchestrator/src/orchestrator/pipeline.py`:

```python
"""Pipeline —— FSM 驱动器。把一条 `created` 的变更请求推过
clarify → locate → run → build/serve，每步写状态 + 推 SSE。

设计文档约束：
- 离开 `created` 前 acquire 配额槽位；进入失败终态时 release。
- 到 `preview-ready` 时 pipeline.run() 结束 —— 槽位仍被占用，由后续 merge/discard/expire 释放。
- 阻塞型 git 操作用 asyncio.to_thread 包装。
- coding 阶段只 create_branch，改代码+commit 委托给 dev_runner.run。
"""
import asyncio
import traceback

from orchestrator.adapters.interfaces import (
    DevRunnerAdapter,
    InteractionSkill,
    PreviewAdapter,
    StackAdapter,
)
from orchestrator.adapters.types import RawRequest
from orchestrator.events import Event, EventBus
from orchestrator.git_manager import GitManager
from orchestrator.interaction_channel import SSEInteractionChannel
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


class _PhaseError(Exception):
    """流水线某一步失败 —— 携带 phase / reason / log。"""

    def __init__(self, phase: str, reason: str, log: str):
        self.phase = phase
        self.reason = reason
        self.log = log
        super().__init__(f"{phase}: {reason}")


class Pipeline:
    def __init__(
        self,
        repo_path: str,
        repository: ChangeRequestRepository,
        git_manager: GitManager,
        event_bus: EventBus,
        quota: QuotaManager,
        interaction_skill: InteractionSkill,
        stack_adapter: StackAdapter,
        dev_runner: DevRunnerAdapter,
        preview_adapter: PreviewAdapter,
    ):
        self.repo_path = repo_path
        self.repository = repository
        self.git_manager = git_manager
        self.event_bus = event_bus
        self.quota = quota
        self.interaction_skill = interaction_skill
        self.stack_adapter = stack_adapter
        self.dev_runner = dev_runner
        self.preview_adapter = preview_adapter
        self._channels: dict[str, SSEInteractionChannel] = {}

    def channel_for(self, request_id: str) -> SSEInteractionChannel:
        """暴露某请求的交互通道，供 REST /answer 端点回填答案。"""
        return self._channels[request_id]

    async def _set_state(self, request_id: str, state: State) -> None:
        self.repository.transition(request_id, state)
        await self.event_bus.publish(
            request_id, Event(type="status", data={"state": state.value})
        )

    async def run(self, request_id: str) -> None:
        """驱动一条 `created` 请求。异常路径统一收敛到 _PhaseError → mark_failed。"""
        await self.quota.acquire(request_id)
        try:
            # created → clarifying
            await self._set_state(request_id, State.CLARIFYING)
            channel = SSEInteractionChannel(request_id, self.event_bus)
            self._channels[request_id] = channel
            cr = self.repository.get(request_id)
            raw = RawRequest(
                url=cr.url,
                screenshot_b64=cr.screenshot_b64,
                box_coords=cr.box_coords,
                viewport=cr.viewport,
                request_text=cr.request_text,
            )
            brief = await self.interaction_skill.clarify(raw, channel)

            # clarifying → located
            locate_result = await self.stack_adapter.locate(raw.url)
            if not locate_result.entry_files:
                raise _PhaseError(
                    "located", "no-route-match", f"URL 未匹配任何路由: {raw.url}"
                )
            await self._set_state(request_id, State.LOCATED)

            # located → coding
            branch = f"cr/{request_id}"
            await asyncio.to_thread(self.git_manager.create_branch, branch)
            self.repository.set_branch(request_id, branch)
            await self._set_state(request_id, State.CODING)
            ctx = await self.stack_adapter.context_pack(locate_result, raw, brief)
            try:
                run_result = await self.dev_runner.run(self.repo_path, branch, ctx)
            except Exception as exc:  # noqa: BLE001
                raise _PhaseError(
                    "coding", "runner-error", "".join(traceback.format_exception(exc))
                ) from exc
            if not run_result.changed:
                raise _PhaseError("coding", "no-changes", run_result.log)

            # coding → building
            await self._set_state(request_id, State.BUILDING)
            build_result = await self.stack_adapter.build(self.repo_path, branch)
            if not build_result.ok:
                raise _PhaseError("building", "build-failed", build_result.log)
            try:
                instance = await self.preview_adapter.serve(self.repo_path, branch)
            except Exception as exc:  # noqa: BLE001
                raise _PhaseError(
                    "building", "container", "".join(traceback.format_exception(exc))
                ) from exc

            # building → preview-ready
            self.repository.set_preview(
                request_id, url=instance.url, handle=instance.handle
            )
            await self._set_state(request_id, State.PREVIEW_READY)
            # 注意：到此 pipeline 结束，槽位仍占用，由 merge/discard/expire 释放
        except _PhaseError as pe:
            self.repository.mark_failed(
                request_id, phase=pe.phase, reason=pe.reason, log=pe.log
            )
            await self.event_bus.publish(
                request_id,
                Event(
                    type="status",
                    data={
                        "state": State.FAILED.value,
                        "phase": pe.phase,
                        "reason": pe.reason,
                    },
                ),
            )
            self.quota.release(request_id)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_pipeline.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/pipeline.py orchestrator/tests/test_pipeline.py
git commit -m "feat: orchestrator 流水线编排器"
```

---

## Task 13: FastAPI app + REST 端点

**Files:**
- Create: `orchestrator/src/orchestrator/schemas.py`
- Create: `orchestrator/src/orchestrator/main.py`
- Modify: `orchestrator/tests/conftest.py`
- Test: `orchestrator/tests/test_api.py`

- [ ] **Step 1: 写 schemas.py**

Create `orchestrator/src/orchestrator/schemas.py`:

```python
from pydantic import BaseModel


class CreateChangeRequestIn(BaseModel):
    url: str
    screenshot_b64: str
    box_coords: dict
    viewport: dict
    request_text: str


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
        )
```

- [ ] **Step 2: 写 main.py**

Create `orchestrator/src/orchestrator/main.py`:

```python
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
    yield


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
```

> 说明：SSE 端点 `GET /change-requests/{id}/events` 已一并写在此处；Task 14 只补针对它的测试。

- [ ] **Step 3: 扩展 conftest.py 加 orchestrator_repo + client fixture**

Modify `orchestrator/tests/conftest.py` — append these two fixtures at the end of the file:

```python


@pytest.fixture
def orchestrator_repo(tmp_path):
    """给 orchestrator 操作用的一次性 git 仓库（有 main + 一个初始提交）。"""
    import subprocess

    repo = tmp_path / "target-repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(
        ["git", *a], cwd=repo, check=True, capture_output=True
    )
    run("init", "-b", "main")
    run("config", "user.email", "test@test")
    run("config", "user.name", "test")
    (repo / "file.txt").write_text("v1\n")
    run("add", "file.txt")
    run("commit", "-m", "init")
    return repo


@pytest.fixture
def client(test_engine, db_session, orchestrator_repo, monkeypatch):
    from fastapi.testclient import TestClient

    from orchestrator import main as main_mod
    from orchestrator.db import get_db
    from orchestrator.events import EventBus
    from orchestrator.main import app, app_state
    from orchestrator.quota import QuotaManager

    # orchestrator 操作一次性 temp 仓库，不碰真实项目仓库
    monkeypatch.setattr(main_mod.settings, "demo_repo_path", str(orchestrator_repo))

    # 每个测试用全新的事件总线 + 配额，避免跨测试串味
    app_state.event_bus = EventBus()
    app_state.quota = QuotaManager(capacity=5)
    app_state.pipeline = None
    app_state.session_factory = lambda: db_session

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 4: 写测试**

Create `orchestrator/tests/test_api.py`:

```python
import time


def _payload(text="把保存按钮改成蓝色"):
    return {
        "url": "http://x/orders",
        "screenshot_b64": "img",
        "box_coords": {"x": 1, "y": 2, "width": 3, "height": 4},
        "viewport": {"width": 1280, "height": 800},
        "request_text": text,
    }


def _wait_state(client, request_id, target, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/change-requests/{request_id}")
        if resp.json()["state"] == target:
            return resp.json()
        time.sleep(0.05)
    raise AssertionError(
        f"{request_id} 未在 {timeout}s 内到达 {target}，"
        f"当前 {client.get(f'/change-requests/{request_id}').json()['state']}"
    )


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_change_request_returns_record(client):
    resp = client.post("/change-requests", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"]
    assert body["state"] in (
        "created", "clarifying", "located", "coding", "building", "preview-ready"
    )


def test_create_then_pipeline_reaches_preview_ready(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    final = _wait_state(client, rid, "preview-ready")
    assert final["branch"] == f"cr/{rid}"
    assert final["preview_url"] is not None


def test_get_missing_returns_404(client):
    assert client.get("/change-requests/nope").status_code == 404


def test_merge_from_preview_ready(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "preview-ready")
    resp = client.post(f"/change-requests/{rid}/merge")
    assert resp.status_code == 200
    assert resp.json()["state"] == "merged"


def test_merge_not_allowed_before_preview_ready(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    resp = client.post(f"/change-requests/{rid}/merge")
    # 要么 409（还没就绪），要么已经就绪并 200 —— 两者都可接受，但不能 500
    assert resp.status_code in (200, 409)


def test_discard_marks_discarded(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "preview-ready")
    resp = client.post(f"/change-requests/{rid}/discard")
    assert resp.status_code == 200
    assert resp.json()["state"] == "discarded"


def test_retry_on_non_terminal_rejected(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "preview-ready")
    # preview-ready 不是 failed/expired → retry 应 409
    assert client.post(f"/change-requests/{rid}/retry").status_code == 409
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_api.py -v`
Expected: PASS — 8 passed

> 本任务实现（schemas/main）先于测试写（同一任务内的安排）。TDD 的「RED」在于测试是新写、此前从未通过；关键是本步骤 PASS。

- [ ] **Step 6: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/schemas.py orchestrator/src/orchestrator/main.py orchestrator/tests/conftest.py orchestrator/tests/test_api.py
git commit -m "feat: orchestrator FastAPI app 与 REST 端点"
```

---

## Task 14: SSE 端点测试

**Files:**
- Test: `orchestrator/tests/test_api_sse.py`

> `main.py` 里的 SSE 端点 `GET /change-requests/{id}/events` 已在 Task 13 写好。本任务只补针对它的测试。`client` fixture（Task 13 建的）已经把 orchestrator 指向一次性 temp git 仓库，所以流水线能正常跑到 preview-ready。

- [ ] **Step 1: 写测试**

Create `orchestrator/tests/test_api_sse.py`:

```python
import json
import time


def _payload():
    return {
        "url": "http://x/orders",
        "screenshot_b64": "img",
        "box_coords": {},
        "viewport": {},
        "request_text": "把保存按钮改成蓝色",
    }


def _parse_sse(text: str) -> list[dict]:
    """把 SSE 原始响应文本解析成 [{event, data}] 列表。"""
    events = []
    cur_event = None
    cur_data = None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            cur_data = line[len("data:"):].strip()
        elif line == "" and cur_event is not None and cur_data is not None:
            events.append({"event": cur_event, "data": json.loads(cur_data)})
            cur_event = None
            cur_data = None
    return events


def _wait_state(client, rid, target, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.get(f"/change-requests/{rid}").json()["state"] == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"{rid} 未到达 {target}")


def test_sse_replays_status_events_after_pipeline_done(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "preview-ready")
    # 此时 SSE 应能回放从 clarifying 到 preview-ready 的全部 status 事件
    with client.stream("GET", f"/change-requests/{rid}/events") as resp:
        chunk = b""
        for raw in resp.iter_bytes():
            chunk += raw
            if b"preview-ready" in chunk:
                break
    events = _parse_sse(chunk.decode())
    states = [e["data"]["state"] for e in events if e["event"] == "status"]
    assert "clarifying" in states
    assert "preview-ready" in states


def test_sse_emits_question_events_during_clarification(client, monkeypatch):
    # 把 app 装配的 InteractionSkill 换成会问 1 个问题的版本
    from orchestrator import main as main_mod
    from orchestrator.adapters.fakes import (
        FakeDevRunner,
        FakeInteractionSkill,
        FakePreviewAdapter,
        FakeStackAdapter,
    )
    from orchestrator.git_manager import GitManager
    from orchestrator.pipeline import Pipeline
    from orchestrator.repository import ChangeRequestRepository

    def build_with_question(self, db):
        return Pipeline(
            repo_path=main_mod.settings.demo_repo_path,
            repository=ChangeRequestRepository(db),
            git_manager=GitManager(main_mod.settings.demo_repo_path),
            event_bus=self.event_bus,
            quota=self.quota,
            interaction_skill=FakeInteractionSkill(question_count=1),
            stack_adapter=FakeStackAdapter(),
            dev_runner=FakeDevRunner(),
            preview_adapter=FakePreviewAdapter(),
        )

    monkeypatch.setattr(main_mod.AppState, "build_pipeline", build_with_question)

    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "clarifying")
    # SSE 里应有一个 question 事件
    with client.stream("GET", f"/change-requests/{rid}/events") as resp:
        chunk = b""
        for raw in resp.iter_bytes():
            chunk += raw
            if b"question" in chunk:
                break
    events = _parse_sse(chunk.decode())
    questions = [e for e in events if e["event"] == "question"]
    assert len(questions) >= 1
    qid = questions[0]["data"]["question_id"]
    # 回答它，流水线应能继续推进到 preview-ready
    client.post(
        f"/change-requests/{rid}/answer",
        json={"question_id": qid, "answer": "更显眼"},
    )
    _wait_state(client, rid, "preview-ready")
    assert client.get(f"/change-requests/{rid}").json()["state"] == "preview-ready"
```

- [ ] **Step 2: 运行测试确认通过（含 Task 13 的 API 测试，确保没回归）**

Run: `cd orchestrator && venv/bin/pytest tests/test_api.py tests/test_api_sse.py -v`
Expected: PASS — test_api(8) + test_api_sse(2) = 10 passed

- [ ] **Step 3: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/tests/test_api_sse.py
git commit -m "test: orchestrator SSE 端点测试"
```

---

## Task 15: 闲置回收器

**Files:**
- Create: `orchestrator/src/orchestrator/reaper.py`
- Test: `orchestrator/tests/test_reaper.py`

- [ ] **Step 1: 写失败测试**

Create `orchestrator/tests/test_reaper.py`:

```python
from datetime import datetime, timedelta

from orchestrator.adapters.fakes import FakePreviewAdapter
from orchestrator.adapters.types import RawRequest
from orchestrator.quota import QuotaManager
from orchestrator.reaper import reap_idle_previews
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


def _raw():
    return RawRequest(
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={},
        viewport={},
        request_text="x",
    )


def _drive_to_preview_ready(repo, cr_id):
    for s in (
        State.CLARIFYING, State.LOCATED, State.CODING, State.BUILDING, State.PREVIEW_READY
    ):
        repo.transition(cr_id, s)


async def test_reap_marks_stale_preview_ready_as_expired(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    _drive_to_preview_ready(repo, cr.id)
    repo.set_preview(cr.id, url="http://x:5101", handle="h1")
    # 把 last_activity 拨到 2 小时前
    obj = repo.get(cr.id)
    obj.last_activity_at = datetime.utcnow() - timedelta(hours=2)
    db_session.commit()

    quota = QuotaManager(capacity=5)
    await quota.acquire(cr.id)
    preview = FakePreviewAdapter()
    preview.live_handles.add("h1")

    reaped = await reap_idle_previews(
        repository=repo, quota=quota, preview_adapter=preview, ttl_seconds=3600
    )

    assert cr.id in reaped
    assert repo.get(cr.id).state == State.EXPIRED.value
    assert quota.in_use() == 0  # 槽位被释放
    assert "h1" not in preview.live_handles  # 预览实例被拆


async def test_reap_leaves_fresh_preview_ready_alone(db_session):
    repo = ChangeRequestRepository(db_session)
    cr = repo.create(_raw())
    _drive_to_preview_ready(repo, cr.id)
    repo.set_preview(cr.id, url="http://x:5101", handle="h2")

    quota = QuotaManager(capacity=5)
    await quota.acquire(cr.id)
    preview = FakePreviewAdapter()
    preview.live_handles.add("h2")

    reaped = await reap_idle_previews(
        repository=repo, quota=quota, preview_adapter=preview, ttl_seconds=3600
    )

    assert cr.id not in reaped
    assert repo.get(cr.id).state == State.PREVIEW_READY.value
    assert quota.in_use() == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd orchestrator && venv/bin/pytest tests/test_reaper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.reaper'`

- [ ] **Step 3: 写 reaper.py**

Create `orchestrator/src/orchestrator/reaper.py`:

```python
"""IdleReaper —— 把闲置超时的 preview-ready 请求标 expired、拆预览、释放配额。

`reap_idle_previews` 是一次扫描（可测试）；`run_reaper_loop` 是 lifespan 里跑的后台循环。
"""
import asyncio

from orchestrator.adapters.interfaces import PreviewAdapter
from orchestrator.adapters.types import PreviewInstance
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


async def reap_idle_previews(
    repository: ChangeRequestRepository,
    quota: QuotaManager,
    preview_adapter: PreviewAdapter,
    ttl_seconds: int,
) -> list[str]:
    """扫一遍 stale 的 preview-ready 请求，逐个 expire。返回被回收的 request id 列表。"""
    reaped: list[str] = []
    for cr in repository.list_stale_previews(older_than_seconds=ttl_seconds):
        if cr.preview_handle:
            instance = PreviewInstance(
                preview_id="", url=cr.preview_url or "", handle=cr.preview_handle
            )
            try:
                await preview_adapter.teardown(instance)
            except Exception:  # noqa: BLE001  best-effort 清理
                pass
        repository.transition(cr.id, State.EXPIRED)
        quota.release(cr.id)
        reaped.append(cr.id)
    return reaped


async def run_reaper_loop(
    session_factory,
    quota: QuotaManager,
    preview_adapter: PreviewAdapter,
    ttl_seconds: int,
    interval_seconds: int,
) -> None:
    """后台循环：每 interval 秒扫一次。lifespan 启动它，取消时干净退出。"""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            db = session_factory()
            try:
                await reap_idle_previews(
                    repository=ChangeRequestRepository(db),
                    quota=quota,
                    preview_adapter=preview_adapter,
                    ttl_seconds=ttl_seconds,
                )
            finally:
                db.close()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001  单次扫描失败不应杀死循环
            continue
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_reaper.py -v`
Expected: PASS — 2 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/reaper.py orchestrator/tests/test_reaper.py
git commit -m "feat: orchestrator 闲置回收器"
```

---

## Task 16: 接线 reaper 到 lifespan + 重启恢复测试

**Files:**
- Modify: `orchestrator/src/orchestrator/main.py`
- Test: `orchestrator/tests/test_lifespan.py`

- [ ] **Step 1: 在 main.py 的 lifespan 里启动 reaper**

Modify `orchestrator/src/orchestrator/main.py` — replace the entire `lifespan` function with this version (adds reaper background-task startup + clean shutdown; keeps the restart-recovery logic and the injectable `session_factory`):

```python
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
```

- [ ] **Step 2: 写失败测试**

Create `orchestrator/tests/test_lifespan.py`:

```python
from orchestrator.adapters.types import RawRequest
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


def test_restart_recovery_marks_non_terminal_as_interrupted(
    test_engine, db_session, orchestrator_repo, monkeypatch
):
    """lifespan 启动时应把残留的非终态请求标 failed(interrupted)。"""
    # 先在 DB 里塞一条「卡在 coding」的请求（模拟上次进程崩溃残留）
    repo = ChangeRequestRepository(db_session)
    raw = RawRequest(
        url="http://x/orders", screenshot_b64="i", box_coords={},
        viewport={}, request_text="x",
    )
    cr = repo.create(raw)
    for s in (State.CLARIFYING, State.LOCATED, State.CODING):
        repo.transition(cr.id, s)
    assert repo.get(cr.id).state == State.CODING.value

    # 起 TestClient（触发 lifespan）
    from fastapi.testclient import TestClient

    from orchestrator import main as main_mod
    from orchestrator.db import get_db
    from orchestrator.events import EventBus
    from orchestrator.main import app, app_state
    from orchestrator.quota import QuotaManager

    monkeypatch.setattr(main_mod.settings, "demo_repo_path", str(orchestrator_repo))
    app_state.event_bus = EventBus()
    app_state.quota = QuotaManager(capacity=5)
    app_state.pipeline = None
    app_state.session_factory = lambda: db_session

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app):
            pass  # 进入再退出，lifespan 完整跑一遍（recovery + reaper 启动/取消）
    finally:
        app.dependency_overrides.clear()

    refreshed = repo.get(cr.id)
    assert refreshed.state == State.FAILED.value
    assert refreshed.fail_phase == "interrupted"
    assert refreshed.fail_reason == "orchestrator-restart"
```

- [ ] **Step 3: 运行测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_lifespan.py tests/test_api.py tests/test_api_sse.py -v`
Expected: PASS — test_lifespan(1) + test_api(8) + test_api_sse(2) = 11 passed

> `test_api.py` / `test_api_sse.py` 的 `client` fixture 已设置 `app_state.session_factory = lambda: db_session`，所以它们的 lifespan recovery 也走测试库，互不干扰。确认这两个文件仍全绿。

- [ ] **Step 4: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/src/orchestrator/main.py orchestrator/tests/test_lifespan.py
git commit -m "feat: orchestrator reaper 接线与重启恢复"
```

---

## Task 17: 端到端集成测试 + README

**Files:**
- Test: `orchestrator/tests/test_integration.py`
- Create: `orchestrator/README.md`

> **实现说明（给执行者）：** `FakeDevRunner` 不在 git 工作树里产生真实文件改动，`Pipeline` 在 `coding` 阶段也不自己 commit（改代码+commit 是 DevRunnerAdapter 的职责）。于是分支 `cr/<id>` 与 `main` 内容完全相同 —— `merge_to_main` 的 `rebase` + `merge --ff-only` 对「无差异分支」是干净的 no-op 合并，会成功。所以端到端测试能到 `merged`。这是 fake 阶段的预期行为；Plan 3 的真实 `DevRunnerAdapter` 会真正改文件并 commit。

- [ ] **Step 1: 写端到端集成测试**

Create `orchestrator/tests/test_integration.py`:

```python
"""端到端：用 fake adapter 把一条变更请求从 created 一路驱动到 merged，
覆盖完整 FSM + REST + SSE + git 合并。
"""
import json
import time


def _payload(text="把订单列表的标题改大一号"):
    return {
        "url": "http://x/orders",
        "screenshot_b64": "img",
        "box_coords": {"x": 10, "y": 20, "width": 100, "height": 40},
        "viewport": {"width": 1440, "height": 900},
        "request_text": text,
    }


def _wait(client, rid, target, timeout=8.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/change-requests/{rid}").json()["state"]
        if last == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"{rid} 卡在 {last}，未到 {target}")


def test_full_lifecycle_created_to_merged(client, orchestrator_repo):
    # 1. 创建变更请求
    rid = client.post("/change-requests", json=_payload()).json()["id"]

    # 2. 流水线（fake adapter）自动推进到 preview-ready
    _wait(client, rid, "preview-ready")
    detail = client.get(f"/change-requests/{rid}").json()
    assert detail["branch"] == f"cr/{rid}"
    assert detail["preview_url"]

    # 3. SSE 能回放完整状态轨迹
    with client.stream("GET", f"/change-requests/{rid}/events") as resp:
        chunk = b""
        for raw in resp.iter_bytes():
            chunk += raw
            if b"preview-ready" in chunk:
                break
    states = []
    for line in chunk.decode().splitlines():
        if line.startswith("data:"):
            try:
                states.append(json.loads(line[5:].strip()).get("state"))
            except json.JSONDecodeError:
                pass
    for expected in ("clarifying", "located", "coding", "building", "preview-ready"):
        assert expected in states, f"SSE 缺少状态 {expected}"

    # 4. 业务员确认合并
    merged = client.post(f"/change-requests/{rid}/merge").json()
    assert merged["state"] == "merged"


def test_full_lifecycle_with_discard(client):
    rid = client.post("/change-requests", json=_payload("丢弃这条")).json()["id"]
    _wait(client, rid, "preview-ready")
    discarded = client.post(f"/change-requests/{rid}/discard").json()
    assert discarded["state"] == "discarded"
```

- [ ] **Step 2: 运行端到端测试确认通过**

Run: `cd orchestrator && venv/bin/pytest tests/test_integration.py -v`
Expected: PASS — 2 passed

- [ ] **Step 3: 运行全部 orchestrator 测试**

Run: `cd orchestrator && venv/bin/pytest -v`
Expected: PASS — config(2) + models(2) + states(7) + adapter_types(6) + interfaces(6) + fakes(6) + repository(9) + git_manager(6) + events(5) + quota(4) + interaction_channel(3) + pipeline(8) + api(8) + api_sse(2) + reaper(2) + lifespan(1) + integration(2) = **79 passed**

- [ ] **Step 4: 写 README.md**

Create `orchestrator/README.md`:

````markdown
# Orchestrator（骨架）

AI 原生低代码平台的大脑 —— 一个 FastAPI 单体服务，把变更请求驱动过完整 FSM
（`created → clarifying → located → coding → building → preview-ready → merged`）。

**Plan 2 阶段：** 4 个 adapter 用 fake 实现装配，编排机器（状态机、REST、SSE、
git、配额、回收器）是完整真实的。Plan 3 把 fake 换成真实 adapter —— 唯一改动点是
`main.py` 的 `AppState.build_pipeline`。

## 运行

需要一个 MySQL（复用 demo 的容器 `demo-mysql`，`localhost:3307`，root/demopass）：

```bash
docker start demo-mysql || docker run -d --name demo-mysql \
  -e MYSQL_ROOT_PASSWORD=demopass -p 3307:3306 mysql:8
docker exec demo-mysql mysql -uroot -pdemopass \
  -e "CREATE DATABASE IF NOT EXISTS orchestrator; CREATE DATABASE IF NOT EXISTS orchestrator_test;"

cd orchestrator
python3 -m venv venv && venv/bin/pip install -e ".[dev]"
venv/bin/uvicorn orchestrator.main:app --port 9000
```

## REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/change-requests` | 创建变更请求，后台启动流水线 |
| GET | `/change-requests/{id}` | 查当前状态（SSE 断了拉这个） |
| GET | `/change-requests/{id}/events` | SSE 状态流（含历史回放） |
| POST | `/change-requests/{id}/answer` | 回答澄清问题 |
| POST | `/change-requests/{id}/merge` | preview-ready → 合并分支 |
| POST | `/change-requests/{id}/discard` | 丢弃（删分支、释放槽位） |
| POST | `/change-requests/{id}/retry` | failed/expired → 创建新请求重跑 |

## 测试

```bash
cd orchestrator
venv/bin/pytest          # 79 passed
```

## 架构

- `states.py` — FSM 定义（状态、合法转换、终态）
- `repository.py` — ChangeRequest 的 MySQL 持久化 + 受 FSM 约束的状态转换
- `git_manager.py` — 对目标仓库的真实 git 操作（切分支/提交/rebase/合并/删分支）
- `events.py` — 每请求一个 SSE 事件总线（含历史回放）
- `quota.py` — 并发槽位信号量（幂等 release）
- `interaction_channel.py` — adapter「问业务员」↔ SSE/answer 端点的桥
- `pipeline.py` — FSM 驱动器，串起 clarify→locate→run→build/serve
- `reaper.py` — 闲置 preview-ready 的回收
- `adapters/` — 4 个 Protocol 接口 + 共享类型 + fake 实现
- `main.py` — FastAPI app、REST + SSE 端点、lifespan（重启恢复 + reaper 启动）
````

- [ ] **Step 5: 提交**

```bash
cd /Users/weizhanhao/doskill
git add orchestrator/tests/test_integration.py orchestrator/README.md
git commit -m "feat: orchestrator 端到端集成测试与 README"
```

---

## 验收标准（Plan 2 完成定义）

- [ ] `cd orchestrator && venv/bin/pytest` —— 79 passed
- [ ] 完整 FSM 可跑：`POST /change-requests` → fake 流水线自动到 `preview-ready` → `POST /merge` → `merged`
- [ ] 所有失败路径有测试覆盖：locate 失败、dev runner 崩/无产出、build 失败、preview 失败、合并冲突
- [ ] SSE 端点能回放历史 + 实时推 `status`/`question`/`variants` 事件
- [ ] 配额信号量、闲置回收器、重启恢复、retry/discard 均有测试
- [ ] 4 个 adapter 的 Protocol 接口 + 共享类型已定义（Plan 3 的契约就位）
- [ ] 所有任务已提交，`git status` 干净（无 `venv/`、`__pycache__/`、`*.egg-info/`）

---

## 交接给 Plan 3 的产物

- `orchestrator/src/orchestrator/adapters/interfaces.py` —— 4 个 Protocol 接口，Plan 3 的真实 adapter 必须实现它们
- `orchestrator/src/orchestrator/adapters/types.py` —— 共享类型契约
- `orchestrator/src/orchestrator/adapters/fakes.py` —— Plan 3 可参照 fake 的行为开关来设计真实 adapter 的契约测试
- 一个完整可跑的编排机器：Plan 3 只需在 `main.py` 的 `AppState.build_pipeline` 里把 4 个 fake 换成真实实现
- `main.py` 的 `AppState.build_pipeline` 是 Plan 3 唯一的接线改动点
