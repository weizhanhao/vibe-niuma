"""ReactViteStackAdapter 契约测试 —— 对真实 demo 仓库的 4 条路由做断言。

契约基准（设计文档 §6）：
  /            → Dashboard
  /orders      → OrderList
  /orders/:id  → OrderDetail   （动态路由，难 case）
  /settings    → Settings
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from orchestrator.adapters.impl.react_vite_stack import ReactViteStackAdapter
from orchestrator.adapters.types import BuildResult, LocateResult, RawRequest, RequestBrief


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


# ── multi-repo build tests ──────────────────────────────────────────


def _make_multi_repo(tmp_path: Path, sub_repos: list[tuple[str, bool]]) -> Path:
    """Create a project folder with sub-repos.

    Args:
        tmp_path: Temp directory root.
        sub_repos: List of (name, has_package_json) tuples.

    Returns the project root path.
    """
    project = tmp_path / "project"
    project.mkdir()
    for name, has_pkg in sub_repos:
        repo_dir = project / name
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()  # presence marker only
        if has_pkg:
            (repo_dir / "package.json").write_text('{"name": "' + name + '"}')
        else:
            (repo_dir / "pyproject.toml").write_text('[tool.poetry]\nname = "' + name + '"\n')
    return project


async def test_build_invokes_npm_build_per_frontend_sub_repo(tmp_path):
    """When sub_repos are detected and both have package.json, npm ci + npm run build
    must be called for each, with the correct cwd."""
    project = _make_multi_repo(
        tmp_path,
        [("backend_fe", True), ("frontend", True)],
    )
    adapter = ReactViteStackAdapter(repo_path=str(project))

    collected_calls: list[tuple[list[str], str]] = []

    async def fake_run(cmd: list[str], cwd: Path, label: str, log=None) -> tuple[int, str]:
        collected_calls.append((list(cmd), str(cwd)))
        return 0, f"--- {label} ---\nok\n"

    with patch(
        "orchestrator.adapters.impl.react_vite_stack.discover_sub_repos"
    ) as mock_discover:
        mock_discover.return_value = [
            project / "backend_fe",
            project / "frontend",
        ]
        with patch.object(adapter, "_run_subprocess", side_effect=fake_run):
            res = await adapter.build(repo_path=str(project), branch="main")

    assert res.ok is True, res.log

    cwds_used = [cwd for _, cwd in collected_calls]
    cmds_used = [cmd for cmd, _ in collected_calls]

    # npm ci and npm run build should each have been called for both sub-repos
    npm_ci_cwds = [cwd for cmd, cwd in collected_calls if cmd == ["npm", "ci"]]
    npm_build_cwds = [cwd for cmd, cwd in collected_calls if cmd == ["npm", "run", "build"]]

    assert str(project / "backend_fe") in npm_ci_cwds
    assert str(project / "frontend") in npm_ci_cwds
    assert str(project / "backend_fe") in npm_build_cwds
    assert str(project / "frontend") in npm_build_cwds


async def test_build_skips_backend_sub_repo(tmp_path):
    """Sub-repos without package.json (backend-like) must be skipped; npm only
    called for the frontend sub-repo that has package.json."""
    project = _make_multi_repo(
        tmp_path,
        [("backend", False), ("frontend", True)],
    )
    adapter = ReactViteStackAdapter(repo_path=str(project))

    collected_calls: list[tuple[list[str], str]] = []

    async def fake_run(cmd: list[str], cwd: Path, label: str, log=None) -> tuple[int, str]:
        collected_calls.append((list(cmd), str(cwd)))
        return 0, f"--- {label} ---\nok\n"

    with patch(
        "orchestrator.adapters.impl.react_vite_stack.discover_sub_repos"
    ) as mock_discover:
        mock_discover.return_value = [
            project / "backend",
            project / "frontend",
        ]
        with patch.object(adapter, "_run_subprocess", side_effect=fake_run):
            res = await adapter.build(repo_path=str(project), branch="main")

    assert res.ok is True, res.log

    cwds_used = [cwd for _, cwd in collected_calls]
    # backend must never appear as a cwd for any npm call
    assert str(project / "backend") not in cwds_used
    # frontend must have been built
    assert str(project / "frontend") in cwds_used


async def test_build_fails_if_any_sub_repo_fails(tmp_path):
    """If any frontend sub-repo build fails, BuildResult.ok must be False; logs
    from all sub-repos must be collected."""
    project = _make_multi_repo(
        tmp_path,
        [("frontend_a", True), ("frontend_b", True)],
    )
    adapter = ReactViteStackAdapter(repo_path=str(project))

    call_count = 0

    async def fake_run(cmd: list[str], cwd: Path, label: str, log=None) -> tuple[int, str]:
        nonlocal call_count
        call_count += 1
        # First sub-repo (frontend_a) succeeds, second (frontend_b) fails on build
        if str(project / "frontend_b") in str(cwd) and cmd == ["npm", "run", "build"]:
            return 1, f"--- {label} ---\nbuild error\n"
        return 0, f"--- {label} ---\nok\n"

    with patch(
        "orchestrator.adapters.impl.react_vite_stack.discover_sub_repos"
    ) as mock_discover:
        mock_discover.return_value = [
            project / "frontend_a",
            project / "frontend_b",
        ]
        with patch.object(adapter, "_run_subprocess", side_effect=fake_run):
            res = await adapter.build(repo_path=str(project), branch="main")

    assert res.ok is False, "Expected False when a sub-repo build fails"
    # Logs from both sub-repos must be present
    assert "frontend_a" in res.log or "npm ci" in res.log
    assert "build error" in res.log


async def test_build_falls_back_to_single_repo_when_no_sub_repos(tmp_path):
    """When discover_sub_repos returns empty, old single-repo path is used:
    npm runs inside <repo_path>/frontend/, not inside any sub-repo."""
    repo = tmp_path / "single_repo"
    repo.mkdir()
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name": "app"}')

    adapter = ReactViteStackAdapter(repo_path=str(repo))

    collected_calls: list[tuple[list[str], str]] = []

    async def fake_run(cmd: list[str], cwd: Path, label: str, log=None) -> tuple[int, str]:
        collected_calls.append((list(cmd), str(cwd)))
        return 0, f"--- {label} ---\nok\n"

    with patch(
        "orchestrator.adapters.impl.react_vite_stack.discover_sub_repos"
    ) as mock_discover:
        mock_discover.return_value = []  # no sub-repos
        with patch.object(adapter, "_run_subprocess", side_effect=fake_run):
            res = await adapter.build(repo_path=str(repo), branch="main")

    assert res.ok is True, res.log
    # npm must have been called with cwd == <repo>/frontend
    frontend_cwds = [cwd for _, cwd in collected_calls]
    assert str(frontend) in frontend_cwds, f"Expected cwd {frontend}, got {frontend_cwds}"
