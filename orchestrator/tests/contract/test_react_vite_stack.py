"""ReactViteStackAdapter 契约测试 —— 对真实 demo 仓库的 4 条路由做断言。

契约基准（设计文档 §6）：
  /            → Dashboard
  /orders      → OrderList
  /orders/:id  → OrderDetail   （动态路由，难 case）
  /settings    → Settings
"""
from orchestrator.adapters.impl.react_vite_stack import ReactViteStackAdapter


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
