"""ReactViteStackAdapter 契约测试 —— 对真实 demo 仓库的 4 条路由做断言。

契约基准（设计文档 §6）：
  /            → Dashboard
  /orders      → OrderList
  /orders/:id  → OrderDetail   （动态路由，难 case）
  /settings    → Settings
"""
import pytest

from orchestrator.adapters.impl.react_vite_stack import ReactViteStackAdapter
from orchestrator.adapters.types import LocateResult, RawRequest, RequestBrief


def _adapter(repo):
    return ReactViteStackAdapter(repo_path=str(repo))


async def test_locate_root_route(demo_repo_copy):
    res = await _adapter(demo_repo_copy).locate("http://demo.local/")
    assert any("Dashboard" in f for f in res.entry_files), res
    assert res.route_path == "/"


async def test_locate_static_route_orders(demo_repo_copy):
    res = await _adapter(demo_repo_copy).locate("http://demo.local/orders")
    assert any("OrderList" in f for f in res.entry_files), res
    assert res.route_path == "/orders"


async def test_locate_dynamic_route_order_detail(demo_repo_copy):
    res = await _adapter(demo_repo_copy).locate("http://demo.local/orders/42")
    assert any("OrderDetail" in f for f in res.entry_files), res
    assert res.route_path == "/orders/:id"


async def test_locate_settings_route(demo_repo_copy):
    res = await _adapter(demo_repo_copy).locate("http://demo.local/settings")
    assert any("Settings" in f for f in res.entry_files), res
    assert res.route_path == "/settings"


async def test_locate_unmatched_url_returns_empty(demo_repo_copy):
    res = await _adapter(demo_repo_copy).locate("http://demo.local/no-such-page")
    assert res.entry_files == []


async def test_locate_returns_paths_relative_to_repo(demo_repo_copy):
    res = await _adapter(demo_repo_copy).locate("http://demo.local/orders")
    # 路径应是相对仓库根的，例如 frontend/src/pages/OrderList.tsx
    for f in res.entry_files:
        assert not f.startswith("/"), f"应是相对路径: {f}"
        assert "frontend" in f


# ── context_pack ────────────────────────────────────────────────────

def _raw():
    return RawRequest(
        url="http://demo.local/orders",
        screenshot_b64="img-b64",
        box_coords={"x": 1, "y": 2, "width": 3, "height": 4},
        viewport={"width": 1280, "height": 800},
        request_text="把订单状态徽章改得更醒目",
    )


async def test_context_pack_includes_entry_files_and_brief(demo_repo_copy):
    a = _adapter(demo_repo_copy)
    locate = await a.locate("http://demo.local/orders")
    brief = RequestBrief(original_text="原始", clarifications=[])
    ctx = await a.context_pack(locate, _raw(), brief)
    assert ctx.brief is brief
    assert ctx.locate_result is locate
    assert ctx.screenshot_b64 == "img-b64"
    assert ctx.box_coords["width"] == 3
    # 入口文件被读出来
    assert len(ctx.entry_file_contents) >= 1
    any_orders = next(iter(k for k in ctx.entry_file_contents if "OrderList" in k), None)
    assert any_orders is not None, ctx.entry_file_contents.keys()
    assert "OrderList" in ctx.entry_file_contents[any_orders]


async def test_context_pack_expands_local_imports(demo_repo_copy):
    """OrderList 引用了 OrderTable 组件 + api/client 模块；至少展开到一层。"""
    a = _adapter(demo_repo_copy)
    locate = await a.locate("http://demo.local/orders")
    ctx = await a.context_pack(
        locate, _raw(), RequestBrief(original_text="x", clarifications=[])
    )
    # 期望除入口文件外还包含至少一个本地依赖
    assert len(ctx.entry_file_contents) >= 2, list(ctx.entry_file_contents)


async def test_context_pack_unmatched_locate_returns_empty_contents(demo_repo_copy):
    a = _adapter(demo_repo_copy)
    empty = LocateResult(entry_files=[], route_path="")
    ctx = await a.context_pack(
        empty, _raw(), RequestBrief(original_text="x", clarifications=[])
    )
    assert ctx.entry_file_contents == {}


# ── build ───────────────────────────────────────────────────────────

async def test_build_returns_no_steps_when_repo_has_no_frontend_or_backend(tmp_path):
    a = ReactViteStackAdapter(repo_path=str(tmp_path))
    res = await a.build(repo_path=str(tmp_path), branch="main")
    assert res.ok is True
    assert "no build steps" in res.log


@pytest.mark.slow
async def test_build_succeeds_on_clean_demo(demo_repo_copy):
    """需要本地有 npm + 网络（或已缓存的 npm 包）。"""
    a = _adapter(demo_repo_copy)
    res = await a.build(repo_path=str(demo_repo_copy), branch="main")
    assert res.ok, res.log[-2000:]


@pytest.mark.slow
async def test_build_fails_on_broken_python_syntax(demo_repo_copy):
    backend = demo_repo_copy / "backend"
    if not backend.is_dir():
        pytest.skip("demo 没有 backend 目录")
    bad = backend / "_broken_syntax.py"
    bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
    a = _adapter(demo_repo_copy)
    res = await a.build(repo_path=str(demo_repo_copy), branch="main")
    # 不要求 frontend 一定成功（可能没装），但 backend 编译不过应该让 ok=False
    if "compileall backend" in res.log:
        assert res.ok is False, res.log[-2000:]
