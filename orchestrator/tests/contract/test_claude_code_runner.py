"""ClaudeCodeDevRunner 契约测试 —— mock 子进程，验证胶水逻辑。

不真打 claude CLI；只验证：argv 组装、env 注入、退出码→异常映射、超时、跑完后
通过 _dev_runner_common 判定 changed + commit。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.impl.claude_code_runner import ClaudeCodeDevRunner, build_prompt
from orchestrator.adapters.types import (
    DevContext, LocateResult, RawRequest, RequestBrief,
)


@pytest.fixture
def tmp_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "f.txt").write_text("a\n")
    run("add", "f.txt")
    run("commit", "-m", "init")
    run("checkout", "-b", "cr/x")
    return repo


def _ctx() -> DevContext:
    return DevContext(
        brief=RequestBrief(original_text="把保存按钮改蓝", clarifications=[]),
        locate_result=LocateResult(
            entry_files=["frontend/src/pages/Settings.tsx"], route_path="/settings"
        ),
        screenshot_b64="img",
        box_coords={"x": 1, "y": 2, "width": 3, "height": 4},
        entry_file_contents={"frontend/src/pages/Settings.tsx": "export const Settings = ...;"},
    )


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"", *, hang: bool = False):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            import asyncio
            await asyncio.sleep(99)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _exec_returning(captured, *, returncode=0, stdout=b"", stderr=b"", hang=False):
    async def _fake(*argv, cwd=None, env=None, **kw):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        captured["env"] = env
        return _FakeProc(returncode, stdout, stderr, hang=hang)
    return _fake


# ── argv / env ──────────────────────────────────────────────────────

async def test_run_invokes_claude_with_base_url_and_model(tmp_repo, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _exec_returning(captured, returncode=0, stdout=b"ok"),
    )
    runner = ClaudeCodeDevRunner(
        anthropic_base_url="http://proxy:8787",
        api_key="sk-x", model="qwen-max",
    )
    await runner.run(str(tmp_repo), "cr/x", _ctx())
    assert captured["argv"][0] == "claude"
    assert "-p" in captured["argv"]
    assert "--model" in captured["argv"]
    assert "qwen-max" in captured["argv"]
    assert captured["env"]["ANTHROPIC_BASE_URL"] == "http://proxy:8787"
    assert captured["env"]["ANTHROPIC_API_KEY"] == "sk-x"
    assert captured["cwd"] == str(tmp_repo)


async def test_run_passes_devcontext_into_prompt(tmp_repo, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _exec_returning(captured, returncode=0),
    )
    await ClaudeCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())
    p_idx = captured["argv"].index("-p")
    prompt = captured["argv"][p_idx + 1]
    assert "把保存按钮改蓝" in prompt
    assert "frontend/src/pages/Settings.tsx" in prompt
    assert "框选区域" in prompt


# ── 退出码 / 超时 ────────────────────────────────────────────────────

async def test_run_nonzero_exit_raises(tmp_repo, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _exec_returning(captured, returncode=1, stderr=b"boom"),
    )
    with pytest.raises(RuntimeError, match="非 0 退出"):
        await ClaudeCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())


async def test_run_timeout_raises(tmp_repo, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _exec_returning(captured, returncode=0, hang=True),
    )
    runner = ClaudeCodeDevRunner(timeout_seconds=1)
    with pytest.raises(RuntimeError, match="超时"):
        await runner.run(str(tmp_repo), "cr/x", _ctx())


async def test_run_cli_missing_raises(tmp_repo, monkeypatch):
    async def _missing(*a, **kw):
        raise FileNotFoundError("no claude")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _missing)
    with pytest.raises(RuntimeError, match="找不到"):
        await ClaudeCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())


# ── changed 判定 ────────────────────────────────────────────────────

async def test_run_zero_exit_with_diff_returns_changed(tmp_repo, monkeypatch):
    captured: dict = {}
    async def _fake(*argv, cwd=None, env=None, **kw):
        captured["argv"] = list(argv)
        # 模拟 claude 真的改了一个文件（但没 commit）
        (Path(cwd) / "g.txt").write_text("new\n")
        return _FakeProc(0, b"changed", b"")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake)
    res = await ClaudeCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())
    assert res.changed is True
    assert res.commit_sha


async def test_run_zero_exit_no_diff_returns_no_changes(tmp_repo, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        _exec_returning(captured, returncode=0, stdout=b"nothing to do"),
    )
    res = await ClaudeCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())
    assert res.changed is False
    assert res.commit_sha is None


# ── prompt builder 单测 ─────────────────────────────────────────────

def test_build_prompt_contains_brief_files_and_box():
    p = build_prompt(_ctx())
    assert "把保存按钮改蓝" in p
    assert "Settings.tsx" in p
    assert "框选区域" in p
    assert "git" in p
