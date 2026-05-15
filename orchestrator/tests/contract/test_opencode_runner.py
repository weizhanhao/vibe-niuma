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


class _FakeStream:
    """假 StreamReader：一次性返回 payload 然后 EOF；hang=True 永不返回。"""

    def __init__(self, payload: bytes, *, hang: bool = False):
        self._payload = payload
        self._hang = hang
        self._emitted = False

    async def readline(self) -> bytes:
        if self._hang:
            import asyncio
            await asyncio.sleep(99)
        if self._emitted:
            return b""
        self._emitted = True
        return self._payload


class _FakeProc:
    def __init__(self, returncode, stdout=b"", stderr=b"", *, hang=False):
        self.returncode = returncode
        # Phase F：runner 已切到 stream_subprocess（按行 readline）。
        self.stdout = _FakeStream(stdout, hang=hang)
        self.stderr = _FakeStream(stderr, hang=False)
        self._hang = hang
    def kill(self):
        # 真实 asyncio.subprocess.kill 让随后 wait() 立刻返回；fake 也照做。
        self._hang = False
    async def wait(self):
        if self._hang:
            import asyncio
            await asyncio.sleep(99)
        return self.returncode


def _exec(captured, **kw):
    async def _f(*argv, cwd=None, env=None, **_):
        captured.update(argv=list(argv), cwd=cwd, env=env)
        return _FakeProc(**kw)
    return _f


async def test_run_invokes_opencode_with_provider_envs(tmp_repo, monkeypatch):
    captured: dict = {}
    # 真实部署中 opencode_runner 会优先用 os.environ 的真 provider key（systemd
    # EnvironmentFile 注入），缺时回退 self._api_key。本测试想验「缺时回退」分支，
    # 所以显式清掉 dev 机器 / CI 上的真 key。
    for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY",
              "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
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
