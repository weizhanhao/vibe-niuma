"""契约测试共用夹具：复制 demo 仓库的精简副本到 tmp_path。"""
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def demo_repo_copy(tmp_path) -> Path:
    """把真实 demo 仓库（不含 node_modules / dist / .git）复制到 tmp_path 下。

    返回复制后的根路径（含 frontend / backend 等）。Adapter 在副本上做改动，
    不污染源仓库。"""
    src = Path(__file__).resolve().parents[3] / "demo"
    if not src.exists():
        pytest.skip(f"demo 仓库不存在于 {src}，跳过此契约测试")
    dst = tmp_path / "demo"
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(
            "node_modules", "dist", "build", ".git",
            "__pycache__", "*.pyc", "venv", ".venv",
        ),
    )
    return dst
