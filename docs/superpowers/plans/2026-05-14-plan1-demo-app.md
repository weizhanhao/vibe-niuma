# Plan 1: Demo 应用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建「订单管理 mini 应用」—— AI 原生低代码平台 MVP 中「被改的目标产品」，同时是后续 `ReactViteStackAdapter` 与各项集成测试的测试夹具。

**Architecture:** 前后端分离。前端 React + Vite + React Router，4 条路由（`/`、`/orders`、`/orders/:id`、`/settings`）。后端 Python + FastAPI + SQLAlchemy + MySQL，提供订单与设置的 mock 数据接口。前后端各带 Dockerfile，`docker-compose.yml` 编排前端 + 后端 + MySQL，供 `DockerPreviewAdapter` 后续使用。

**Tech Stack:** React 19、Vite 6、React Router 7、TypeScript、Vitest + Testing Library；Python 3.11、FastAPI、SQLAlchemy 2.x、PyMySQL、Pydantic v2、pytest + httpx；MySQL 8；Docker Compose。

**本计划用到的「需要你提供的清单」项：** #9（npm/pip 是否需国内镜像源 —— 计划默认用官方源，如需镜像在执行时改 `.npmrc` / `pip.conf`）、#10（业务场景，默认订单管理）、#11（UI 风格，默认干净内部工具风）。其余清单项（ECS、模型、端口、域名、远程仓库）Plan 1 不需要，留给 Plan 2-5。

---

## File Structure

```
demo/
├── docker-compose.yml              # 编排 frontend + backend + mysql
├── backend/
│   ├── pyproject.toml              # 依赖与项目元数据
│   ├── Dockerfile                  # 后端镜像
│   ├── .env.example                # 环境变量样例
│   ├── src/demo_backend/
│   │   ├── __init__.py
│   │   ├── config.py               # Settings：DATABASE_URL 等
│   │   ├── db.py                   # SQLAlchemy engine/session、Base
│   │   ├── models.py               # ORM 模型：Order、OrderItem、AppSetting
│   │   ├── schemas.py              # Pydantic 出入参 schema
│   │   ├── seed.py                 # 写入 mock 数据
│   │   ├── main.py                 # FastAPI app、CORS、路由挂载、启动 seed
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── orders.py           # GET /api/orders、GET /api/orders/{id}
│   │       └── settings.py         # GET /api/settings、PUT /api/settings/{key}
│   └── tests/
│       ├── conftest.py             # 测试用 MySQL 库、TestClient fixture
│       ├── test_config.py
│       ├── test_models.py
│       ├── test_schemas.py
│       ├── test_orders.py
│       ├── test_settings.py
│       └── test_main.py
└── frontend/
    ├── package.json
    ├── vite.config.ts              # Vite 配置 + Vitest 配置
    ├── tsconfig.json
    ├── tsconfig.node.json
    ├── index.html
    ├── Dockerfile                  # 前端镜像（dev server 模式）
    ├── .npmrc                      # registry 配置（默认官方源）
    ├── src/
    │   ├── main.tsx                # 入口，挂载 RouterProvider
    │   ├── router.tsx              # 路由表 —— StackAdapter 的契约锚点
    │   ├── vite-env.d.ts
    │   ├── api/
    │   │   └── client.ts           # fetch 封装 + 类型
    │   ├── styles/
    │   │   └── tokens.css          # 设计 token
    │   ├── components/
    │   │   ├── Layout.tsx          # 侧边导航 + 内容区
    │   │   ├── StatCard.tsx        # 看板统计卡片
    │   │   └── OrderTable.tsx      # 订单表格
    │   └── pages/
    │       ├── Dashboard.tsx       # 路由 /
    │       ├── OrderList.tsx       # 路由 /orders
    │       ├── OrderDetail.tsx     # 路由 /orders/:id
    │       └── Settings.tsx        # 路由 /settings
    └── tests/
        ├── setup.ts                # Vitest setup（jest-dom）
        ├── api-client.test.ts
        ├── StatCard.test.tsx
        ├── OrderTable.test.tsx
        ├── Layout.test.tsx
        ├── pages.test.tsx
        └── router.test.tsx
```

**前置约定（每个任务都假定已满足）：**
- 所有命令在 `demo/` 下相应子目录执行，路径以仓库根 `/Users/weizhanhao/doskill` 为基准。
- 后端测试需要一个可连的 MySQL。Task 3 起会用 `docker run` 起一个临时 MySQL 容器（`demo-mysql`），Task 15 后改用 `docker compose`。测试连接串走环境变量 `TEST_DATABASE_URL`，conftest 默认 `mysql+pymysql://root:demopass@localhost:3306/demo_test`。
- 提交信息用约定式提交（feat/test/chore/docs）。

---

## Task 1: 后端项目骨架与配置

**Files:**
- Create: `demo/backend/pyproject.toml`
- Create: `demo/backend/.env.example`
- Create: `demo/backend/src/demo_backend/__init__.py`
- Create: `demo/backend/src/demo_backend/config.py`
- Test: `demo/backend/tests/test_config.py`

- [ ] **Step 1: 写 pyproject.toml**

Create `demo/backend/pyproject.toml`:

```toml
[project]
name = "demo-backend"
version = "0.1.0"
description = "订单管理 mini 应用 —— AI 原生低代码平台 MVP 的目标产品/测试夹具"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0",
    "pymysql>=1.1",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
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
```

- [ ] **Step 2: 写 .env.example**

Create `demo/backend/.env.example`:

```
DATABASE_URL=mysql+pymysql://root:demopass@localhost:3306/demo
```

- [ ] **Step 3: 写包初始化文件**

Create `demo/backend/src/demo_backend/__init__.py` (empty file — 写入零字节内容)。

- [ ] **Step 4: 写失败测试**

Create `demo/backend/tests/test_config.py`:

```python
from demo_backend.config import Settings


def test_settings_reads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@h:3306/db")
    settings = Settings()
    assert settings.database_url == "mysql+pymysql://u:p@h:3306/db"


def test_settings_has_default_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings()
    assert settings.database_url.startswith("mysql+pymysql://")
```

- [ ] **Step 5: 运行测试确认失败**

Run: `cd demo/backend && pip install -e ".[dev]" && pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'demo_backend.config'`

- [ ] **Step 6: 写 config.py**

Create `demo/backend/src/demo_backend/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://root:demopass@localhost:3306/demo"


settings = Settings()
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd demo/backend && pytest tests/test_config.py -v`
Expected: PASS — 2 passed

- [ ] **Step 8: 提交**

```bash
git add demo/backend/pyproject.toml demo/backend/.env.example demo/backend/src/demo_backend/__init__.py demo/backend/src/demo_backend/config.py demo/backend/tests/test_config.py
git commit -m "feat: demo 后端项目骨架与配置"
```

---

## Task 2: 数据库连接与 ORM 模型

**Files:**
- Create: `demo/backend/src/demo_backend/db.py`
- Create: `demo/backend/src/demo_backend/models.py`
- Test: `demo/backend/tests/test_models.py`

- [ ] **Step 1: 写 db.py**

Create `demo/backend/src/demo_backend/db.py`:

```python
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from demo_backend.config import settings

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

Create `demo/backend/tests/test_models.py`:

```python
from demo_backend.models import AppSetting, Order, OrderItem


def test_order_has_expected_columns():
    cols = set(Order.__table__.columns.keys())
    assert cols == {"id", "customer_name", "status", "total_amount", "created_at"}


def test_order_item_has_expected_columns():
    cols = set(OrderItem.__table__.columns.keys())
    assert cols == {"id", "order_id", "product_name", "quantity", "unit_price"}


def test_app_setting_has_expected_columns():
    cols = set(AppSetting.__table__.columns.keys())
    assert cols == {"key", "value"}
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd demo/backend && pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'demo_backend.models'`

- [ ] **Step 4: 写 models.py**

Create `demo/backend/src/demo_backend/models.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from demo_backend.db import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd demo/backend && pytest tests/test_models.py -v`
Expected: PASS — 3 passed

- [ ] **Step 6: 提交**

```bash
git add demo/backend/src/demo_backend/db.py demo/backend/src/demo_backend/models.py demo/backend/tests/test_models.py
git commit -m "feat: demo 后端数据库连接与 ORM 模型"
```

---

## Task 3: Pydantic schema 与测试夹具

**Files:**
- Create: `demo/backend/src/demo_backend/schemas.py`
- Create: `demo/backend/tests/conftest.py`
- Test: `demo/backend/tests/test_schemas.py`

- [ ] **Step 1: 写失败测试**

Create `demo/backend/tests/test_schemas.py`:

```python
from demo_backend.schemas import OrderDetailOut, OrderItemOut, OrderSummaryOut, SettingOut


def test_order_summary_out_fields():
    s = OrderSummaryOut(
        id=1, customer_name="张三", status="paid", total_amount=99.5
    )
    assert s.id == 1
    assert s.customer_name == "张三"


def test_order_detail_out_includes_items():
    item = OrderItemOut(id=1, product_name="键盘", quantity=2, unit_price=50.0)
    d = OrderDetailOut(
        id=1, customer_name="张三", status="paid", total_amount=100.0, items=[item]
    )
    assert d.items[0].product_name == "键盘"


def test_setting_out_fields():
    s = SettingOut(key="page_title", value="订单管理")
    assert s.key == "page_title"
    assert s.value == "订单管理"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd demo/backend && pytest tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'demo_backend.schemas'`

- [ ] **Step 3: 写 schemas.py**

Create `demo/backend/src/demo_backend/schemas.py`:

```python
from pydantic import BaseModel, ConfigDict


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_name: str
    quantity: int
    unit_price: float


class OrderSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_name: str
    status: str
    total_amount: float


class OrderDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_name: str
    status: str
    total_amount: float
    items: list[OrderItemOut]


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str


class SettingUpdateIn(BaseModel):
    value: str
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd demo/backend && pytest tests/test_schemas.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: 写 conftest.py**

Create `demo/backend/tests/conftest.py`:

```python
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "mysql+pymysql://root:demopass@localhost:3306/demo_test",
)


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    from demo_backend.db import Base
    import demo_backend.models  # noqa: F401  保证模型被注册

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(test_engine):
    session = sessionmaker(bind=test_engine)()
    from demo_backend.models import AppSetting, Order, OrderItem

    session.query(OrderItem).delete()
    session.query(Order).delete()
    session.query(AppSetting).delete()
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(test_engine, db_session):
    from fastapi.testclient import TestClient

    from demo_backend.db import get_db
    from demo_backend.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

> 说明：`conftest.py` 的 `client` fixture 引用 `demo_backend.main`，该模块在 Task 6 创建。Task 3/4/5 只用到 `db_session`（独立可用），`client` 在 Task 6 后才被实际调用。

- [ ] **Step 6: 起临时 MySQL 并建库**

Run: `docker run -d --name demo-mysql -e MYSQL_ROOT_PASSWORD=demopass -p 3306:3306 mysql:8`
Run: `sleep 25`（等待 MySQL 就绪）
Run: `docker exec demo-mysql mysql -uroot -pdemopass -e "CREATE DATABASE IF NOT EXISTS demo_test; CREATE DATABASE IF NOT EXISTS demo;"`
Expected: 无报错

- [ ] **Step 7: 运行已有测试确认整体仍通过**

Run: `cd demo/backend && pytest tests/test_config.py tests/test_models.py tests/test_schemas.py -v`
Expected: PASS — 8 passed

- [ ] **Step 8: 提交**

```bash
git add demo/backend/src/demo_backend/schemas.py demo/backend/tests/conftest.py demo/backend/tests/test_schemas.py
git commit -m "feat: demo 后端 Pydantic schema 与测试夹具"
```

---

## Task 4: 订单路由

**Files:**
- Create: `demo/backend/src/demo_backend/routers/__init__.py`
- Create: `demo/backend/src/demo_backend/routers/orders.py`
- Test: `demo/backend/tests/test_orders.py`

- [ ] **Step 1: 写包初始化文件**

Create `demo/backend/src/demo_backend/routers/__init__.py` (empty file — 写入零字节内容)。

- [ ] **Step 2: 写失败测试**

Create `demo/backend/tests/test_orders.py`:

```python
from demo_backend.models import Order, OrderItem


def _make_order(db_session, customer_name="张三", status="paid"):
    order = Order(customer_name=customer_name, status=status, total_amount=100.0)
    order.items = [
        OrderItem(product_name="键盘", quantity=1, unit_price=60.0),
        OrderItem(product_name="鼠标", quantity=2, unit_price=20.0),
    ]
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def test_list_orders_returns_summaries(client, db_session):
    _make_order(db_session, customer_name="张三")
    _make_order(db_session, customer_name="李四")

    resp = client.get("/api/orders")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {o["customer_name"] for o in body} == {"张三", "李四"}
    assert "items" not in body[0]


def test_get_order_detail_includes_items(client, db_session):
    order = _make_order(db_session)

    resp = client.get(f"/api/orders/{order.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_name"] == "张三"
    assert len(body["items"]) == 2
    assert {i["product_name"] for i in body["items"]} == {"键盘", "鼠标"}


def test_get_order_detail_404_when_missing(client, db_session):
    resp = client.get("/api/orders/99999")
    assert resp.status_code == 404
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd demo/backend && pytest tests/test_orders.py -v`
Expected: FAIL — collection error（`demo_backend.main` 还不存在，`client` fixture 无法构造）

- [ ] **Step 4: 写 orders.py**

Create `demo/backend/src/demo_backend/routers/orders.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from demo_backend.db import get_db
from demo_backend.models import Order
from demo_backend.schemas import OrderDetailOut, OrderSummaryOut

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("", response_model=list[OrderSummaryOut])
def list_orders(db: Session = Depends(get_db)) -> list[Order]:
    return db.query(Order).order_by(Order.id).all()


@router.get("/{order_id}", response_model=OrderDetailOut)
def get_order(order_id: int, db: Session = Depends(get_db)) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order
```

> 测试在 Task 6（`main.py` 创建并挂载路由）后才能跑通；本任务到此先不运行测试。

- [ ] **Step 5: 提交**

```bash
git add demo/backend/src/demo_backend/routers/__init__.py demo/backend/src/demo_backend/routers/orders.py demo/backend/tests/test_orders.py
git commit -m "feat: demo 后端订单路由"
```

---

## Task 5: 设置路由

**Files:**
- Create: `demo/backend/src/demo_backend/routers/settings.py`
- Test: `demo/backend/tests/test_settings.py`

- [ ] **Step 1: 写失败测试**

Create `demo/backend/tests/test_settings.py`:

```python
from demo_backend.models import AppSetting


def test_list_settings_returns_all(client, db_session):
    db_session.add(AppSetting(key="page_title", value="订单管理"))
    db_session.add(AppSetting(key="rows_per_page", value="20"))
    db_session.commit()

    resp = client.get("/api/settings")

    assert resp.status_code == 200
    body = resp.json()
    assert {s["key"]: s["value"] for s in body} == {
        "page_title": "订单管理",
        "rows_per_page": "20",
    }


def test_update_setting_changes_value(client, db_session):
    db_session.add(AppSetting(key="page_title", value="订单管理"))
    db_session.commit()

    resp = client.put("/api/settings/page_title", json={"value": "订单中心"})

    assert resp.status_code == 200
    assert resp.json()["value"] == "订单中心"
    refreshed = client.get("/api/settings").json()
    assert {s["key"]: s["value"] for s in refreshed}["page_title"] == "订单中心"


def test_update_setting_404_when_missing(client, db_session):
    resp = client.put("/api/settings/nope", json={"value": "x"})
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd demo/backend && pytest tests/test_settings.py -v`
Expected: FAIL — collection error（`demo_backend.main` 还不存在）

- [ ] **Step 3: 写 settings.py**

Create `demo/backend/src/demo_backend/routers/settings.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from demo_backend.db import get_db
from demo_backend.models import AppSetting
from demo_backend.schemas import SettingOut, SettingUpdateIn

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=list[SettingOut])
def list_settings(db: Session = Depends(get_db)) -> list[AppSetting]:
    return db.query(AppSetting).order_by(AppSetting.key).all()


@router.put("/{key}", response_model=SettingOut)
def update_setting(
    key: str, payload: SettingUpdateIn, db: Session = Depends(get_db)
) -> AppSetting:
    setting = db.get(AppSetting, key)
    if setting is None:
        raise HTTPException(status_code=404, detail="设置项不存在")
    setting.value = payload.value
    db.commit()
    db.refresh(setting)
    return setting
```

- [ ] **Step 4: 提交**

```bash
git add demo/backend/src/demo_backend/routers/settings.py demo/backend/tests/test_settings.py
git commit -m "feat: demo 后端设置路由"
```

---

## Task 6: FastAPI 应用入口与 seed 数据

**Files:**
- Create: `demo/backend/src/demo_backend/seed.py`
- Create: `demo/backend/src/demo_backend/main.py`
- Test: `demo/backend/tests/test_main.py`

- [ ] **Step 1: 写 seed.py**

Create `demo/backend/src/demo_backend/seed.py`:

```python
from sqlalchemy.orm import Session

from demo_backend.models import AppSetting, Order, OrderItem

_ORDERS = [
    ("张三", "paid", [("机械键盘", 1, 299.0), ("无线鼠标", 1, 99.0)]),
    ("李四", "pending", [("27寸显示器", 2, 1299.0)]),
    ("王五", "shipped", [("USB-C 扩展坞", 1, 359.0), ("HDMI 线", 3, 29.0)]),
    ("赵六", "paid", [("人体工学椅", 1, 1599.0)]),
    ("钱七", "cancelled", [("笔记本支架", 1, 159.0)]),
]

_SETTINGS = {
    "page_title": "订单管理",
    "rows_per_page": "20",
    "currency_symbol": "¥",
}


def seed_if_empty(db: Session) -> None:
    if db.query(Order).count() == 0:
        for customer_name, status, items in _ORDERS:
            order_items = [
                OrderItem(product_name=p, quantity=q, unit_price=u)
                for p, q, u in items
            ]
            total = sum(q * u for _, q, u in items)
            db.add(
                Order(
                    customer_name=customer_name,
                    status=status,
                    total_amount=total,
                    items=order_items,
                )
            )
    if db.query(AppSetting).count() == 0:
        for key, value in _SETTINGS.items():
            db.add(AppSetting(key=key, value=value))
    db.commit()
```

- [ ] **Step 2: 写 main.py**

Create `demo/backend/src/demo_backend/main.py`:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from demo_backend.db import Base, SessionLocal, engine
from demo_backend.routers import orders, settings
from demo_backend.seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()
    yield


app = FastAPI(title="订单管理 mini 应用", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(orders.router)
app.include_router(settings.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 3: 写失败测试**

Create `demo/backend/tests/test_main.py`:

```python
def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 4: 运行全部后端测试确认通过**

Run: `cd demo/backend && pytest -v`
Expected: PASS — test_config(2) + test_models(3) + test_schemas(3) + test_orders(3) + test_settings(3) + test_main(1) = 15 passed

- [ ] **Step 5: 手动冒烟启动**

Run: `cd demo/backend && DATABASE_URL=mysql+pymysql://root:demopass@localhost:3306/demo uvicorn demo_backend.main:app --port 8000 &`
Run: `sleep 3 && curl -s localhost:8000/api/orders | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" && curl -s localhost:8000/api/settings`
Expected: 输出 `5`，随后是 3 条设置的 JSON
Run: `kill %1`（停掉后台 uvicorn）

- [ ] **Step 6: 提交**

```bash
git add demo/backend/src/demo_backend/seed.py demo/backend/src/demo_backend/main.py demo/backend/tests/test_main.py
git commit -m "feat: demo 后端应用入口与 seed 数据"
```

---

## Task 7: 后端 Dockerfile

**Files:**
- Create: `demo/backend/Dockerfile`
- Create: `demo/backend/.dockerignore`

- [ ] **Step 1: 写 .dockerignore**

Create `demo/backend/.dockerignore`:

```
__pycache__/
*.pyc
.pytest_cache/
tests/
.env
*.egg-info/
```

- [ ] **Step 2: 写 Dockerfile**

Create `demo/backend/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "demo_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: 构建镜像验证**

Run: `cd demo/backend && docker build -t demo-backend:test .`
Expected: 构建成功，最后输出含 `naming to docker.io/library/demo-backend:test`

- [ ] **Step 4: 提交**

```bash
git add demo/backend/Dockerfile demo/backend/.dockerignore
git commit -m "chore: demo 后端 Dockerfile"
```

---

## Task 8: 前端项目骨架

**Files:**
- Create: `demo/frontend/package.json`
- Create: `demo/frontend/.npmrc`
- Create: `demo/frontend/tsconfig.json`
- Create: `demo/frontend/tsconfig.node.json`
- Create: `demo/frontend/vite.config.ts`
- Create: `demo/frontend/index.html`
- Create: `demo/frontend/src/vite-env.d.ts`
- Create: `demo/frontend/tests/setup.ts`

- [ ] **Step 1: 写 package.json**

Create `demo/frontend/package.json`:

```json
{
  "name": "demo-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "tsc -b && vite build",
    "preview": "vite preview --host 0.0.0.0",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.1.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.6.0",
    "@testing-library/react": "^16.1.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "jsdom": "^25.0.0",
    "typescript": "^5.7.0",
    "vite": "^6.0.0",
    "vitest": "^2.1.0"
  }
}
```

- [ ] **Step 2: 写 .npmrc**

Create `demo/frontend/.npmrc`:

```
registry=https://registry.npmjs.org/
```

> 清单 #9：若 ECS 无法访问官方 registry，执行时把此处改为 `https://registry.npmmirror.com/`。

- [ ] **Step 3: 写 tsconfig.json**

Create `demo/frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src", "tests"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 4: 写 tsconfig.node.json**

Create `demo/frontend/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 5: 写 vite.config.ts**

Create `demo/frontend/vite.config.ts`:

```typescript
/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
  },
});
```

- [ ] **Step 6: 写 index.html**

Create `demo/frontend/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>订单管理</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 7: 写 vite-env.d.ts**

Create `demo/frontend/src/vite-env.d.ts`:

```typescript
/// <reference types="vite/client" />
```

- [ ] **Step 8: 写 tests/setup.ts**

Create `demo/frontend/tests/setup.ts`:

```typescript
import '@testing-library/jest-dom';
```

- [ ] **Step 9: 安装依赖验证**

Run: `cd demo/frontend && npm install`
Expected: 安装成功，生成 `node_modules/` 与 `package-lock.json`

- [ ] **Step 10: 提交**

```bash
git add demo/frontend/package.json demo/frontend/.npmrc demo/frontend/tsconfig.json demo/frontend/tsconfig.node.json demo/frontend/vite.config.ts demo/frontend/index.html demo/frontend/src/vite-env.d.ts demo/frontend/tests/setup.ts demo/frontend/package-lock.json
git commit -m "chore: demo 前端项目骨架"
```

---

## Task 9: API 客户端

**Files:**
- Create: `demo/frontend/src/api/client.ts`
- Test: `demo/frontend/tests/api-client.test.ts`

- [ ] **Step 1: 写失败测试**

Create `demo/frontend/tests/api-client.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getOrder, getSettings, listOrders, updateSetting } from '../src/api/client';

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(body: unknown, ok = true, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status,
      json: async () => body,
    }),
  );
}

describe('api client', () => {
  it('listOrders 请求 /api/orders 并返回数组', async () => {
    mockFetch([{ id: 1, customer_name: '张三', status: 'paid', total_amount: 100 }]);
    const orders = await listOrders();
    expect(fetch).toHaveBeenCalledWith('/api/orders');
    expect(orders[0].customer_name).toBe('张三');
  });

  it('getOrder 请求 /api/orders/:id', async () => {
    mockFetch({
      id: 7,
      customer_name: '李四',
      status: 'paid',
      total_amount: 50,
      items: [],
    });
    const order = await getOrder(7);
    expect(fetch).toHaveBeenCalledWith('/api/orders/7');
    expect(order.id).toBe(7);
  });

  it('getOrder 在 404 时抛错', async () => {
    mockFetch({ detail: 'not found' }, false, 404);
    await expect(getOrder(999)).rejects.toThrow();
  });

  it('getSettings 请求 /api/settings', async () => {
    mockFetch([{ key: 'page_title', value: '订单管理' }]);
    const settings = await getSettings();
    expect(fetch).toHaveBeenCalledWith('/api/settings');
    expect(settings[0].key).toBe('page_title');
  });

  it('updateSetting 用 PUT 提交 value', async () => {
    mockFetch({ key: 'page_title', value: '订单中心' });
    const updated = await updateSetting('page_title', '订单中心');
    expect(fetch).toHaveBeenCalledWith('/api/settings/page_title', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: '订单中心' }),
    });
    expect(updated.value).toBe('订单中心');
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd demo/frontend && npm test -- api-client`
Expected: FAIL — `Cannot find module '../src/api/client'`

- [ ] **Step 3: 写 client.ts**

Create `demo/frontend/src/api/client.ts`:

```typescript
export interface OrderSummary {
  id: number;
  customer_name: string;
  status: string;
  total_amount: number;
}

export interface OrderItem {
  id: number;
  product_name: string;
  quantity: number;
  unit_price: number;
}

export interface OrderDetail extends OrderSummary {
  items: OrderItem[];
}

export interface AppSetting {
  key: string;
  value: string;
}

async function parse<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    throw new Error(`请求失败：${resp.status}`);
  }
  return (await resp.json()) as T;
}

export async function listOrders(): Promise<OrderSummary[]> {
  return parse<OrderSummary[]>(await fetch('/api/orders'));
}

export async function getOrder(id: number): Promise<OrderDetail> {
  return parse<OrderDetail>(await fetch(`/api/orders/${id}`));
}

export async function getSettings(): Promise<AppSetting[]> {
  return parse<AppSetting[]>(await fetch('/api/settings'));
}

export async function updateSetting(key: string, value: string): Promise<AppSetting> {
  return parse<AppSetting>(
    await fetch(`/api/settings/${key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    }),
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd demo/frontend && npm test -- api-client`
Expected: PASS — 5 passed

- [ ] **Step 5: 提交**

```bash
git add demo/frontend/src/api/client.ts demo/frontend/tests/api-client.test.ts
git commit -m "feat: demo 前端 API 客户端"
```

---

## Task 10: 设计 token 与展示组件

**Files:**
- Create: `demo/frontend/src/styles/tokens.css`
- Create: `demo/frontend/src/components/StatCard.tsx`
- Create: `demo/frontend/src/components/OrderTable.tsx`
- Test: `demo/frontend/tests/StatCard.test.tsx`
- Test: `demo/frontend/tests/OrderTable.test.tsx`

- [ ] **Step 1: 写 tokens.css**

Create `demo/frontend/src/styles/tokens.css`:

```css
:root {
  --color-bg: #f5f6f8;
  --color-surface: #ffffff;
  --color-border: #e3e5e9;
  --color-text: #1f2329;
  --color-text-muted: #646a73;
  --color-accent: #3370ff;
  --color-accent-text: #ffffff;

  --status-paid: #1f9d55;
  --status-pending: #d9920a;
  --status-shipped: #3370ff;
  --status-cancelled: #8a9099;

  --space-1: 4px;
  --space-2: 8px;
  --space-3: 16px;
  --space-4: 24px;
  --space-5: 40px;

  --radius: 8px;
  --text-sm: 13px;
  --text-base: 14px;
  --text-lg: 18px;
  --text-xl: 28px;

  --font-sans: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}

* {
  box-sizing: border-box;
  margin: 0;
}

body {
  font-family: var(--font-sans);
  font-size: var(--text-base);
  color: var(--color-text);
  background: var(--color-bg);
}
```

- [ ] **Step 2: 写 StatCard 失败测试**

Create `demo/frontend/tests/StatCard.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StatCard } from '../src/components/StatCard';

describe('StatCard', () => {
  it('渲染标题与数值', () => {
    render(<StatCard label="订单总数" value="128" />);
    expect(screen.getByText('订单总数')).toBeInTheDocument();
    expect(screen.getByText('128')).toBeInTheDocument();
  });

  it('带 hint 时渲染 hint 文本', () => {
    render(<StatCard label="本月营收" value="¥9,800" hint="较上月 +12%" />);
    expect(screen.getByText('较上月 +12%')).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd demo/frontend && npm test -- StatCard`
Expected: FAIL — `Cannot find module '../src/components/StatCard'`

- [ ] **Step 4: 写 StatCard.tsx**

Create `demo/frontend/src/components/StatCard.tsx`:

```tsx
interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
}

export function StatCard({ label, value, hint }: StatCardProps) {
  return (
    <div
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius)',
        padding: 'var(--space-4)',
        minWidth: 180,
      }}
    >
      <div style={{ color: 'var(--color-text-muted)', fontSize: 'var(--text-sm)' }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 'var(--text-xl)',
          fontWeight: 600,
          marginTop: 'var(--space-2)',
        }}
      >
        {value}
      </div>
      {hint && (
        <div
          style={{
            color: 'var(--color-text-muted)',
            fontSize: 'var(--text-sm)',
            marginTop: 'var(--space-1)',
          }}
        >
          {hint}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: 运行 StatCard 测试确认通过**

Run: `cd demo/frontend && npm test -- StatCard`
Expected: PASS — 2 passed

- [ ] **Step 6: 写 OrderTable 失败测试**

Create `demo/frontend/tests/OrderTable.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { OrderSummary } from '../src/api/client';
import { OrderTable } from '../src/components/OrderTable';

const ORDERS: OrderSummary[] = [
  { id: 1, customer_name: '张三', status: 'paid', total_amount: 398 },
  { id: 2, customer_name: '李四', status: 'pending', total_amount: 2598 },
];

describe('OrderTable', () => {
  it('每条订单渲染一行，含客户名与金额', () => {
    render(
      <MemoryRouter>
        <OrderTable orders={ORDERS} />
      </MemoryRouter>,
    );
    expect(screen.getByText('张三')).toBeInTheDocument();
    expect(screen.getByText('李四')).toBeInTheDocument();
    expect(screen.getByText('¥398.00')).toBeInTheDocument();
  });

  it('客户名是指向详情页的链接', () => {
    render(
      <MemoryRouter>
        <OrderTable orders={ORDERS} />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: '张三' });
    expect(link).toHaveAttribute('href', '/orders/1');
  });

  it('空列表时渲染空状态文案', () => {
    render(
      <MemoryRouter>
        <OrderTable orders={[]} />
      </MemoryRouter>,
    );
    expect(screen.getByText('暂无订单')).toBeInTheDocument();
  });
});
```

- [ ] **Step 7: 运行测试确认失败**

Run: `cd demo/frontend && npm test -- OrderTable`
Expected: FAIL — `Cannot find module '../src/components/OrderTable'`

- [ ] **Step 8: 写 OrderTable.tsx**

Create `demo/frontend/src/components/OrderTable.tsx`:

```tsx
import { Link } from 'react-router-dom';
import type { OrderSummary } from '../api/client';

const STATUS_LABEL: Record<string, string> = {
  paid: '已支付',
  pending: '待支付',
  shipped: '已发货',
  cancelled: '已取消',
};

const STATUS_COLOR: Record<string, string> = {
  paid: 'var(--status-paid)',
  pending: 'var(--status-pending)',
  shipped: 'var(--status-shipped)',
  cancelled: 'var(--status-cancelled)',
};

interface OrderTableProps {
  orders: OrderSummary[];
}

export function OrderTable({ orders }: OrderTableProps) {
  if (orders.length === 0) {
    return (
      <div style={{ color: 'var(--color-text-muted)', padding: 'var(--space-4)' }}>
        暂无订单
      </div>
    );
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)' }}>
          <th style={{ padding: 'var(--space-2)' }}>订单号</th>
          <th style={{ padding: 'var(--space-2)' }}>客户</th>
          <th style={{ padding: 'var(--space-2)' }}>状态</th>
          <th style={{ padding: 'var(--space-2)' }}>金额</th>
        </tr>
      </thead>
      <tbody>
        {orders.map((order) => (
          <tr key={order.id} style={{ borderTop: '1px solid var(--color-border)' }}>
            <td style={{ padding: 'var(--space-2)' }}>#{order.id}</td>
            <td style={{ padding: 'var(--space-2)' }}>
              <Link to={`/orders/${order.id}`} style={{ color: 'var(--color-accent)' }}>
                {order.customer_name}
              </Link>
            </td>
            <td style={{ padding: 'var(--space-2)' }}>
              <span style={{ color: STATUS_COLOR[order.status] ?? 'var(--color-text)' }}>
                {STATUS_LABEL[order.status] ?? order.status}
              </span>
            </td>
            <td style={{ padding: 'var(--space-2)' }}>
              ¥{order.total_amount.toFixed(2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 9: 运行 OrderTable 测试确认通过**

Run: `cd demo/frontend && npm test -- OrderTable`
Expected: PASS — 3 passed

- [ ] **Step 10: 提交**

```bash
git add demo/frontend/src/styles/tokens.css demo/frontend/src/components/StatCard.tsx demo/frontend/src/components/OrderTable.tsx demo/frontend/tests/StatCard.test.tsx demo/frontend/tests/OrderTable.test.tsx
git commit -m "feat: demo 前端设计 token 与展示组件"
```

---

## Task 11: Layout 组件

**Files:**
- Create: `demo/frontend/src/components/Layout.tsx`
- Test: `demo/frontend/tests/Layout.test.tsx`

- [ ] **Step 1: 写失败测试**

Create `demo/frontend/tests/Layout.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { Layout } from '../src/components/Layout';

describe('Layout', () => {
  it('渲染三个导航链接', () => {
    render(
      <MemoryRouter>
        <Layout>
          <div>内容</div>
        </Layout>
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: '看板' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: '订单' })).toHaveAttribute('href', '/orders');
    expect(screen.getByRole('link', { name: '设置' })).toHaveAttribute(
      'href',
      '/settings',
    );
  });

  it('渲染传入的子内容', () => {
    render(
      <MemoryRouter>
        <Layout>
          <div>内容区文本</div>
        </Layout>
      </MemoryRouter>,
    );
    expect(screen.getByText('内容区文本')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd demo/frontend && npm test -- Layout`
Expected: FAIL — `Cannot find module '../src/components/Layout'`

- [ ] **Step 3: 写 Layout.tsx**

Create `demo/frontend/src/components/Layout.tsx`:

```tsx
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';

const NAV = [
  { to: '/', label: '看板', end: true },
  { to: '/orders', label: '订单', end: false },
  { to: '/settings', label: '设置', end: false },
];

interface LayoutProps {
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <nav
        aria-label="主导航"
        style={{
          width: 200,
          background: 'var(--color-surface)',
          borderRight: '1px solid var(--color-border)',
          padding: 'var(--space-4) var(--space-3)',
        }}
      >
        <div
          style={{
            fontSize: 'var(--text-lg)',
            fontWeight: 600,
            marginBottom: 'var(--space-4)',
          }}
        >
          订单管理
        </div>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            style={({ isActive }) => ({
              display: 'block',
              padding: 'var(--space-2) var(--space-3)',
              borderRadius: 'var(--radius)',
              marginBottom: 'var(--space-1)',
              color: isActive ? 'var(--color-accent)' : 'var(--color-text)',
              background: isActive ? 'var(--color-bg)' : 'transparent',
              textDecoration: 'none',
            })}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main style={{ flex: 1, padding: 'var(--space-5)' }}>{children}</main>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd demo/frontend && npm test -- Layout`
Expected: PASS — 2 passed

- [ ] **Step 5: 提交**

```bash
git add demo/frontend/src/components/Layout.tsx demo/frontend/tests/Layout.test.tsx
git commit -m "feat: demo 前端 Layout 组件"
```

---

## Task 12: 四个页面组件

**Files:**
- Create: `demo/frontend/src/pages/Dashboard.tsx`
- Create: `demo/frontend/src/pages/OrderList.tsx`
- Create: `demo/frontend/src/pages/OrderDetail.tsx`
- Create: `demo/frontend/src/pages/Settings.tsx`
- Test: `demo/frontend/tests/pages.test.tsx`

- [ ] **Step 1: 写失败测试**

Create `demo/frontend/tests/pages.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dashboard } from '../src/pages/Dashboard';
import { OrderDetail } from '../src/pages/OrderDetail';
import { OrderList } from '../src/pages/OrderList';
import { Settings } from '../src/pages/Settings';

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetchSequence(...bodies: unknown[]) {
  const fn = vi.fn();
  for (const body of bodies) {
    fn.mockResolvedValueOnce({ ok: true, status: 200, json: async () => body });
  }
  vi.stubGlobal('fetch', fn);
}

describe('Dashboard', () => {
  it('加载订单后渲染统计卡片', async () => {
    mockFetchSequence([
      { id: 1, customer_name: '张三', status: 'paid', total_amount: 100 },
      { id: 2, customer_name: '李四', status: 'pending', total_amount: 200 },
    ]);
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('订单总数')).toBeInTheDocument());
    expect(screen.getByText('2')).toBeInTheDocument();
  });
});

describe('OrderList', () => {
  it('加载并渲染订单表格', async () => {
    mockFetchSequence([
      { id: 1, customer_name: '张三', status: 'paid', total_amount: 100 },
    ]);
    render(
      <MemoryRouter>
        <OrderList />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('张三')).toBeInTheDocument());
  });
});

describe('OrderDetail', () => {
  it('按路由参数加载订单详情', async () => {
    mockFetchSequence({
      id: 7,
      customer_name: '王五',
      status: 'shipped',
      total_amount: 300,
      items: [{ id: 1, product_name: '显示器', quantity: 1, unit_price: 300 }],
    });
    render(
      <MemoryRouter initialEntries={['/orders/7']}>
        <Routes>
          <Route path="/orders/:id" element={<OrderDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('王五')).toBeInTheDocument());
    expect(screen.getByText('显示器')).toBeInTheDocument();
  });
});

describe('Settings', () => {
  it('加载并渲染设置项', async () => {
    mockFetchSequence([{ key: 'page_title', value: '订单管理' }]);
    render(
      <MemoryRouter>
        <Settings />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByDisplayValue('订单管理')).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd demo/frontend && npm test -- pages`
Expected: FAIL — `Cannot find module '../src/pages/Dashboard'`

- [ ] **Step 3: 写 Dashboard.tsx**

Create `demo/frontend/src/pages/Dashboard.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { listOrders, type OrderSummary } from '../api/client';
import { StatCard } from '../components/StatCard';

export function Dashboard() {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);

  useEffect(() => {
    listOrders()
      .then(setOrders)
      .catch(() => setOrders([]));
  }, []);

  if (orders === null) {
    return <div>加载中…</div>;
  }

  const total = orders.length;
  const revenue = orders
    .filter((o) => o.status === 'paid')
    .reduce((sum, o) => sum + o.total_amount, 0);
  const pending = orders.filter((o) => o.status === 'pending').length;

  return (
    <section aria-labelledby="dashboard-heading">
      <h1 id="dashboard-heading" style={{ marginBottom: 'var(--space-4)' }}>
        看板
      </h1>
      <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
        <StatCard label="订单总数" value={String(total)} />
        <StatCard label="已支付营收" value={`¥${revenue.toFixed(2)}`} />
        <StatCard label="待支付订单" value={String(pending)} />
      </div>
    </section>
  );
}
```

- [ ] **Step 4: 写 OrderList.tsx**

Create `demo/frontend/src/pages/OrderList.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { listOrders, type OrderSummary } from '../api/client';
import { OrderTable } from '../components/OrderTable';

export function OrderList() {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);

  useEffect(() => {
    listOrders()
      .then(setOrders)
      .catch(() => setOrders([]));
  }, []);

  return (
    <section aria-labelledby="orders-heading">
      <h1 id="orders-heading" style={{ marginBottom: 'var(--space-4)' }}>
        订单
      </h1>
      <div
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius)',
          padding: 'var(--space-3)',
        }}
      >
        {orders === null ? <div>加载中…</div> : <OrderTable orders={orders} />}
      </div>
    </section>
  );
}
```

- [ ] **Step 5: 写 OrderDetail.tsx**

Create `demo/frontend/src/pages/OrderDetail.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getOrder, type OrderDetail as OrderDetailData } from '../api/client';

export function OrderDetail() {
  const { id } = useParams<{ id: string }>();
  const [order, setOrder] = useState<OrderDetailData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!id) return;
    getOrder(Number(id))
      .then(setOrder)
      .catch(() => setError(true));
  }, [id]);

  if (error) {
    return <div>订单不存在</div>;
  }
  if (order === null) {
    return <div>加载中…</div>;
  }

  return (
    <section aria-labelledby="order-detail-heading">
      <h1 id="order-detail-heading" style={{ marginBottom: 'var(--space-4)' }}>
        订单 #{order.id}
      </h1>
      <div
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius)',
          padding: 'var(--space-4)',
        }}
      >
        <p style={{ marginBottom: 'var(--space-2)' }}>客户：{order.customer_name}</p>
        <p style={{ marginBottom: 'var(--space-2)' }}>状态：{order.status}</p>
        <p style={{ marginBottom: 'var(--space-3)' }}>
          总金额：¥{order.total_amount.toFixed(2)}
        </p>
        <h2 style={{ fontSize: 'var(--text-lg)', marginBottom: 'var(--space-2)' }}>
          商品明细
        </h2>
        <ul>
          {order.items.map((item) => (
            <li key={item.id}>
              {item.product_name} × {item.quantity} —— ¥{item.unit_price.toFixed(2)}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
```

- [ ] **Step 6: 写 Settings.tsx**

Create `demo/frontend/src/pages/Settings.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { getSettings, updateSetting, type AppSetting } from '../api/client';

export function Settings() {
  const [settings, setSettings] = useState<AppSetting[] | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch(() => setSettings([]));
  }, []);

  function handleChange(key: string, value: string) {
    setSettings((prev) =>
      prev ? prev.map((s) => (s.key === key ? { ...s, value } : s)) : prev,
    );
  }

  async function handleSave(key: string) {
    const target = settings?.find((s) => s.key === key);
    if (!target) return;
    await updateSetting(key, target.value);
    setSaved(key);
    setTimeout(() => setSaved(null), 2000);
  }

  if (settings === null) {
    return <div>加载中…</div>;
  }

  return (
    <section aria-labelledby="settings-heading">
      <h1 id="settings-heading" style={{ marginBottom: 'var(--space-4)' }}>
        设置
      </h1>
      <div
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius)',
          padding: 'var(--space-4)',
          maxWidth: 480,
        }}
      >
        {settings.map((setting) => (
          <div
            key={setting.key}
            style={{
              display: 'flex',
              gap: 'var(--space-2)',
              alignItems: 'center',
              marginBottom: 'var(--space-3)',
            }}
          >
            <label style={{ width: 140, color: 'var(--color-text-muted)' }}>
              {setting.key}
            </label>
            <input
              value={setting.value}
              onChange={(e) => handleChange(setting.key, e.target.value)}
              style={{
                flex: 1,
                padding: 'var(--space-2)',
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--radius)',
              }}
            />
            <button
              type="button"
              onClick={() => handleSave(setting.key)}
              style={{
                padding: 'var(--space-2) var(--space-3)',
                background: 'var(--color-accent)',
                color: 'var(--color-accent-text)',
                border: 'none',
                borderRadius: 'var(--radius)',
                cursor: 'pointer',
              }}
            >
              保存
            </button>
            {saved === setting.key && (
              <span style={{ color: 'var(--status-paid)' }}>已保存</span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd demo/frontend && npm test -- pages`
Expected: PASS — 4 passed

- [ ] **Step 8: 提交**

```bash
git add demo/frontend/src/pages/ demo/frontend/tests/pages.test.tsx
git commit -m "feat: demo 前端四个页面组件"
```

---

## Task 13: 路由表与应用入口

**Files:**
- Create: `demo/frontend/src/router.tsx`
- Create: `demo/frontend/src/main.tsx`
- Test: `demo/frontend/tests/router.test.tsx`

- [ ] **Step 1: 写失败测试**

Create `demo/frontend/tests/router.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest';
import { routes } from '../src/router';

describe('routes 配置', () => {
  it('暴露 4 条路由路径', () => {
    const paths = routes.map((r) => r.path);
    expect(paths).toEqual(['/', '/orders', '/orders/:id', '/settings']);
  });

  it('每条路由都有 element', () => {
    for (const route of routes) {
      expect(route.element).toBeDefined();
    }
  });
});
```

> `router.tsx` 必须导出一个名为 `routes` 的数组，每项形如 `{ path: string, element: ReactElement }`。这是 `ReactViteStackAdapter`（Plan 3）做 URL→源文件映射的契约锚点 —— 路径字符串、顺序、文件位置都不可随意改动。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd demo/frontend && npm test -- router`
Expected: FAIL — `Cannot find module '../src/router'`

- [ ] **Step 3: 写 router.tsx**

Create `demo/frontend/src/router.tsx`:

```tsx
import type { ReactElement } from 'react';
import { createBrowserRouter } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { OrderDetail } from './pages/OrderDetail';
import { OrderList } from './pages/OrderList';
import { Settings } from './pages/Settings';

interface RouteDef {
  path: string;
  element: ReactElement;
}

// routes：StackAdapter 的契约锚点。path 字符串与对应页面组件文件一一对应。
export const routes: RouteDef[] = [
  { path: '/', element: <Dashboard /> },
  { path: '/orders', element: <OrderList /> },
  { path: '/orders/:id', element: <OrderDetail /> },
  { path: '/settings', element: <Settings /> },
];

export const router = createBrowserRouter(
  routes.map((route) => ({
    path: route.path,
    element: <Layout>{route.element}</Layout>,
  })),
);
```

- [ ] **Step 4: 写 main.tsx**

Create `demo/frontend/src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { router } from './router';
import './styles/tokens.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd demo/frontend && npm test -- router`
Expected: PASS — 2 passed

- [ ] **Step 6: 运行全部前端测试 + 类型检查 + 构建**

Run: `cd demo/frontend && npm test`
Expected: PASS — api-client(5) + StatCard(2) + OrderTable(3) + Layout(2) + pages(4) + router(2) = 18 passed

Run: `cd demo/frontend && npm run build`
Expected: 类型检查通过，`dist/` 生成成功

- [ ] **Step 7: 提交**

```bash
git add demo/frontend/src/router.tsx demo/frontend/src/main.tsx demo/frontend/tests/router.test.tsx
git commit -m "feat: demo 前端路由表与应用入口"
```

---

## Task 14: 前端 Dockerfile

**Files:**
- Create: `demo/frontend/Dockerfile`
- Create: `demo/frontend/.dockerignore`

- [ ] **Step 1: 写 .dockerignore**

Create `demo/frontend/.dockerignore`:

```
node_modules/
dist/
tests/
```

- [ ] **Step 2: 写 Dockerfile**

Create `demo/frontend/Dockerfile`:

```dockerfile
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
```

> 说明：MVP 阶段前端预览跑 Vite dev server（热重载，符合 `DockerPreviewAdapter` 的预览语义）。生产构建 `npm run build` 的产物服务方式留到后续 plan。

- [ ] **Step 3: 构建镜像验证**

Run: `cd demo/frontend && docker build -t demo-frontend:test .`
Expected: 构建成功，最后输出含 `naming to docker.io/library/demo-frontend:test`

- [ ] **Step 4: 提交**

```bash
git add demo/frontend/Dockerfile demo/frontend/.dockerignore
git commit -m "chore: demo 前端 Dockerfile"
```

---

## Task 15: docker-compose 编排

**Files:**
- Create: `demo/docker-compose.yml`
- Create: `demo/README.md`

- [ ] **Step 1: 写 docker-compose.yml**

Create `demo/docker-compose.yml`:

```yaml
services:
  mysql:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: demopass
      MYSQL_DATABASE: demo
    ports:
      - "3306:3306"
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-pdemopass"]
      interval: 5s
      timeout: 3s
      retries: 20

  backend:
    build: ./backend
    environment:
      DATABASE_URL: mysql+pymysql://root:demopass@mysql:3306/demo
    ports:
      - "8000:8000"
    depends_on:
      mysql:
        condition: service_healthy

  frontend:
    build: ./frontend
    ports:
      - "5173:5173"
    depends_on:
      - backend
```

- [ ] **Step 2: 写 README.md**

Create `demo/README.md`:

````markdown
# 订单管理 mini 应用（demo）

AI 原生低代码平台 MVP 中「被改的目标产品」，同时是 `ReactViteStackAdapter` 与集成测试的测试夹具。

## 一键启动

```bash
cd demo
docker compose up --build
```

- 前端：http://localhost:5173
- 后端 API：http://localhost:8000/api/health
- MySQL：localhost:3306（root / demopass）

## 路由表

| 路由 | 页面组件 | 文件 |
|---|---|---|
| `/` | Dashboard | `frontend/src/pages/Dashboard.tsx` |
| `/orders` | OrderList | `frontend/src/pages/OrderList.tsx` |
| `/orders/:id` | OrderDetail | `frontend/src/pages/OrderDetail.tsx` |
| `/settings` | Settings | `frontend/src/pages/Settings.tsx` |

路由表定义在 `frontend/src/router.tsx` 的 `routes` 导出 —— StackAdapter 的契约锚点，勿随意改动。

## 本地开发（不走容器）

后端：
```bash
docker run -d --name demo-mysql -e MYSQL_ROOT_PASSWORD=demopass -p 3306:3306 mysql:8
cd backend && pip install -e ".[dev]"
uvicorn demo_backend.main:app --port 8000
```

前端：
```bash
cd frontend && npm install && npm run dev
```

## 测试

后端（需 MySQL 在 localhost:3306，且存在 demo_test 库）：
```bash
cd backend && pytest
```

前端：
```bash
cd frontend && npm test
```
````

- [ ] **Step 3: 端到端验证 compose**

Run: `docker rm -f demo-mysql`（清掉 Task 3 起的临时 MySQL，释放 3306 端口）
Run: `cd demo && docker compose up --build -d`
Run: `sleep 45`（等待 MySQL 健康检查 + 后端 seed + 前端起 dev server）
Run: `curl -s localhost:8000/api/health`
Expected: `{"status":"ok"}`
Run: `curl -s localhost:8000/api/orders | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"`
Expected: `5`
Run: `curl -s localhost:5173 | grep -o '<title>.*</title>'`
Expected: `<title>订单管理</title>`
Run: `cd demo && docker compose down`

- [ ] **Step 4: 提交**

```bash
git add demo/docker-compose.yml demo/README.md
git commit -m "chore: demo docker-compose 编排与说明文档"
```

---

## Task 16: 顶层 .gitignore

**Files:**
- Create: `.gitignore`

- [ ] **Step 1: 写 .gitignore**

Create `/Users/weizhanhao/doskill/.gitignore`:

```
# Python
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
demo/backend/.env

# Node
node_modules/
dist/

# 环境
.DS_Store
```

- [ ] **Step 2: 验证 git 状态干净**

Run: `cd /Users/weizhanhao/doskill && git status --short`
Expected: 仅显示 `.gitignore` 未跟踪，无 `node_modules/`、`__pycache__/` 等噪音

- [ ] **Step 3: 提交**

```bash
git add .gitignore
git commit -m "chore: 顶层 .gitignore"
```

---

## 验收标准（Plan 1 完成定义）

- [ ] `cd demo/backend && pytest` —— 15 passed
- [ ] `cd demo/frontend && npm test` —— 18 passed
- [ ] `cd demo/frontend && npm run build` —— 构建成功
- [ ] `cd demo && docker compose up --build` —— 三个服务起来，前端 5173 可访问、后端 8000 健康、seed 出 5 条订单
- [ ] `frontend/src/router.tsx` 导出 `routes` 数组，含 4 条路由 —— Plan 3 的契约锚点就位
- [ ] 所有任务已提交，`git status` 干净

---

## 交接给 Plan 2 的产物

- 一个可一键启动的 demo 应用，前端 4 路由、后端 REST API、MySQL 持久化
- `routes` 契约锚点（`frontend/src/router.tsx`）
- 前后端 Dockerfile + docker-compose.yml —— Plan 3 的 `DockerPreviewAdapter` 直接复用
- demo 仓库本身将作为 Orchestrator 操作的目标 git 仓库（Plan 2 起，Orchestrator 会对它切分支）
```