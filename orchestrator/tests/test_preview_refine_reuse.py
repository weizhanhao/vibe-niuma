"""Plan 10 Task 7: preview_adapter 加 base_handle 支持。

业务员视角：refine 时大多数情况 Vite 热重载自动接 dev_runner 的文件改动，
不用碰容器。但如果 dev_runner 改了 package.json / vite.config / 环境变量
等 config 文件，hot reload 接不到 → pipeline 显式调 serve(base_handle=...)
触发容器 restart 来保证业务员看到改动。

API：
  await adapter.serve(repo, branch, base_handle="docker-handle-xxx", log=...)
  → 如果 base_handle 非空：docker compose restart（复用容器名 + 端口）
  → 如果 base_handle None：原来的 docker compose up（新容器 + 新端口）
"""
from __future__ import annotations

import pytest

from orchestrator.adapters.fakes import FakePreviewAdapter


@pytest.mark.asyncio
async def test_fake_preview_serve_with_base_handle_reuses_same_instance():
    adapter = FakePreviewAdapter()
    first = await adapter.serve("/repo", "cr/abc")
    assert first.handle is not None
    assert first.url is not None

    second = await adapter.serve("/repo", "cr/abc", base_handle=first.handle)
    assert second.handle == first.handle
    assert second.url == first.url


@pytest.mark.asyncio
async def test_fake_preview_serve_without_base_handle_new_instance():
    adapter = FakePreviewAdapter()
    first = await adapter.serve("/repo", "cr/abc")
    second = await adapter.serve("/repo", "cr/def")
    assert second.handle != first.handle
    assert second.url != first.url


@pytest.mark.asyncio
async def test_serve_signature_accepts_optional_base_handle():
    """contract：所有 PreviewAdapter 实现 serve() 都应能吃 base_handle kwarg。"""
    import inspect

    from orchestrator.adapters.impl.docker_preview import DockerPreviewAdapter

    sig = inspect.signature(DockerPreviewAdapter.serve)
    assert "base_handle" in sig.parameters
    p = sig.parameters["base_handle"]
    assert p.default is None
