"""OpenCodeDevRunner 契约测试 —— 与 claude_code_runner 同构，验证 opencode CLI 的胶水。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.impl.opencode_runner import OpenCodeDevRunner
from orchestrator.adapters.types import DevContext, LocateResult, RequestBrief


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
        brief=RequestBrief(original_text="把表头改大", clarifications=[]),
        locate_result=LocateResult(entry_files=["frontend/src/pages/OrderList.tsx"], route_path="/orders"),
        screenshot_b64="img",
        box_coords={"x": 1, "y": 2, "width": 3, "height": 4},
        entry_file_contents={"frontend/src/pages/OrderList.tsx": "..."},
    )


class _FakeProc:
    def __init__(self, returncode, stdout=b"", stderr=b"", *, hang=False):
        self.returncode = returncode
        self._stdout, self._stderr, self._hang = stdout, stderr, hang
    async def communicate(self):
        if self._hang:
            import asyncio
            await asyncio.sleep(99)
        return self._stdout, self._stderr
    def kill(self): pass
    async def wait(self): return self.returncode


def _exec(captured, **kw):
    async def _f(*argv, cwd=None, env=None, **_):
        captured.update(argv=list(argv), cwd=cwd, env=env)
        return _FakeProc(**kw)
    return _f


async def test_run_invokes_opencode_with_provider_envs(tmp_repo, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _exec(captured, returncode=0, stdout=b"ok"))
    await OpenCodeDevRunner(api_key="sk-x", model="deepseek-chat").run(
        str(tmp_repo), "cr/x", _ctx())
    assert captured["argv"][0] == "opencode"
    assert "run" in captured["argv"]
    assert "--model" in captured["argv"]
    assert "deepseek-chat" in captured["argv"]
    assert captured["env"]["OPENAI_API_KEY"] == "sk-x"
    assert captured["env"]["DEEPSEEK_API_KEY"] == "sk-x"
    assert captured["env"]["DASHSCOPE_API_KEY"] == "sk-x"


async def test_run_passes_devcontext_into_prompt(tmp_repo, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _exec(captured, returncode=0))
    await OpenCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())
    run_idx = captured["argv"].index("run")
    prompt = captured["argv"][run_idx + 1]
    assert "把表头改大" in prompt
    assert "OrderList.tsx" in prompt


async def test_run_nonzero_exit_raises(tmp_repo, monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _exec({}, returncode=1, stderr=b"boom"))
    with pytest.raises(RuntimeError, match="非 0 退出"):
        await OpenCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())


async def test_run_timeout_raises(tmp_repo, monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _exec({}, returncode=0, hang=True))
    with pytest.raises(RuntimeError, match="超时"):
        await OpenCodeDevRunner(timeout_seconds=1).run(str(tmp_repo), "cr/x", _ctx())


async def test_run_cli_missing_raises(tmp_repo, monkeypatch):
    async def _missing(*a, **kw):
        raise FileNotFoundError("no opencode")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _missing)
    with pytest.raises(RuntimeError, match="找不到"):
        await OpenCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())


async def test_run_zero_exit_no_diff_returns_no_changes(tmp_repo, monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec",
                        _exec({}, returncode=0, stdout=b"nothing to do"))
    res = await OpenCodeDevRunner().run(str(tmp_repo), "cr/x", _ctx())
    assert res.changed is False
