"""DockerPreviewAdapter 契约测试。

- 端口分配 / 记账：mock subprocess，快测，默认就跑。
- 真起容器 / 真拆容器：标 @pytest.mark.docker，需要 Docker daemon 才跑。
"""
import shutil
import subprocess

import pytest

from orchestrator.adapters.impl.docker_preview import DockerPreviewAdapter
from orchestrator.adapters.types import PreviewInstance


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=3).returncode == 0
    except Exception:
        return False


# ── 端口分配（不依赖 docker）─────────────────────────────────────────

def test_allocate_port_in_configured_range():
    a = DockerPreviewAdapter(port_min=9301, port_max=9305)
    p = a._allocate_port()
    assert 9301 <= p <= 9305


def test_allocate_port_skips_already_held():
    a = DockerPreviewAdapter(port_min=9311, port_max=9313)
    a._used_ports["fake-handle"] = 9311
    p = a._allocate_port()
    assert p != 9311


def test_allocate_port_raises_when_exhausted():
    a = DockerPreviewAdapter(port_min=9321, port_max=9322)
    a._used_ports["a"] = 9321
    a._used_ports["b"] = 9322
    with pytest.raises(RuntimeError):
        a._allocate_port()


def test_release_port_is_idempotent():
    a = DockerPreviewAdapter(port_min=9331, port_max=9333)
    a._used_ports["x"] = 9331
    a._release_port("x")
    a._release_port("x")  # 再次释放不报错
    a._release_port("nonexistent")
    assert a._used_ports == {}


# ── 真 Docker（默认 deselected）──────────────────────────────────────

@pytest.mark.docker
@pytest.mark.skipif(not _docker_available(), reason="需要 Docker daemon")
async def test_serve_and_teardown_real_container(demo_repo_copy):
    # Plan 8 Task 8：demo 默认带 compose 文件，但本地 dev 没有 vibe-niuma-net 外部网络。
    # 这个 contract 测试只覆盖单 Dockerfile 老路径 —— 干掉 compose 文件落回老路径。
    compose = demo_repo_copy / "docker-compose.preview.yml"
    if compose.exists():
        compose.unlink()
    a = DockerPreviewAdapter(
        port_min=9401, port_max=9420, preview_host="localhost", internal_port=5173,
    )
    inst = await a.serve(repo_path=str(demo_repo_copy), branch="main")
    try:
        assert inst.url.startswith("http://localhost:")
        assert 9401 <= int(inst.url.rsplit(":", 1)[1]) <= 9420
        assert inst.handle
    finally:
        await a.teardown(inst)
    # 拆完端口被释放
    assert inst.handle not in a._used_ports


@pytest.mark.docker
@pytest.mark.skipif(not _docker_available(), reason="需要 Docker daemon")
async def test_teardown_idempotent_on_unknown_handle():
    a = DockerPreviewAdapter()
    fake = PreviewInstance(preview_id="x", url="http://x", handle="vibe-niuma-no-such-container")
    # 不抛异常即可
    await a.teardown(fake)
