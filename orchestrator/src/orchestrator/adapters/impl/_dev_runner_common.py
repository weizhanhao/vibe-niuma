"""DevRunner 共用层 —— 两个 dev runner 实现都需要的纯逻辑。

功能：
- has_changes(repo_path, branch)：分支是否相对 main 有任何改动（含未提交 + 已提交）
- commit_all(repo_path, branch, message)：在分支上 add -A + commit，返回 SHA
- collect_log(stdout, stderr, limit)：截尾日志（保留尾部）
- make_run_result(repo_path, branch, log)：跑完后判定 changed + 自动 commit
- stream_subprocess(proc, log)：并行 drain stdout/stderr，每行调用 log

约定：Pipeline 在 `coding` 阶段已经把工作树切到了 `branch`（git_manager.create_branch
做的 `checkout -b`），所以这里所有命令都假定 HEAD 已是 branch。
"""
from __future__ import annotations

import asyncio
import subprocess

from orchestrator.adapters.types import LogSink, RunResult


def _git(repo_path: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo_path, check=check,
        capture_output=True, text=True,
    )


def has_changes(repo_path: str, branch: str) -> bool:
    """branch 相对 main 是否有差异（含未提交 + 已提交 commits）。"""
    porcelain = _git(repo_path, "status", "--porcelain").stdout.strip()
    if porcelain:
        return True
    # 跟 main 比有没有新 commit
    diff = _git(
        repo_path, "rev-list", "--count", f"main..{branch}", check=False,
    )
    if diff.returncode == 0:
        try:
            count = int((diff.stdout or "0").strip() or 0)
        except ValueError:
            count = 0
        return count > 0
    return False


def commit_all(repo_path: str, branch: str, message: str) -> str:
    """add -A + commit，返回 commit SHA；若无可提交则不创建空 commit、返回当前 HEAD。"""
    porcelain = _git(repo_path, "status", "--porcelain").stdout.strip()
    if not porcelain:
        return _git(repo_path, "rev-parse", "HEAD").stdout.strip()
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-m", message)
    return _git(repo_path, "rev-parse", "HEAD").stdout.strip()


def collect_log(stdout: str, stderr: str, limit: int = 8000) -> str:
    """合并 stdout + stderr，超长保留尾部（dev runner 的失败原因往往在末尾）。"""
    combined = (stdout or "") + ("\n" + stderr if stderr else "")
    if len(combined) <= limit:
        return combined
    keep = limit - 80
    return f"... <truncated {len(combined) - keep} chars> ...\n" + combined[-keep:]


def make_run_result(repo_path: str, branch: str, log: str, message: str) -> RunResult:
    """跑完后：有改动 → commit_all + RunResult(changed=True)；无改动 → RunResult(changed=False)。"""
    if not has_changes(repo_path, branch):
        return RunResult(changed=False, commit_sha=None, log=log + "\n[no changes]")
    sha = commit_all(repo_path, branch, message)
    return RunResult(changed=True, commit_sha=sha, log=log)


async def stream_subprocess(
    proc: asyncio.subprocess.Process,
    log: LogSink,
    *,
    timeout: float | None = None,
    heartbeat_seconds: float = 5.0,
) -> tuple[str, str]:
    """读 proc.stdout/stderr 直到结束，每读一行 await log(line)；返回累计 stdout/stderr。

    并行 drain 两路；空行跳过 log，但仍计入累计。`timeout` 是 gather + wait 总时长上限。
    `heartbeat_seconds`：子进程超过该秒数没产出任何行时，定期发 "⏳ runner 静默 Xs…"
    心跳，避免 cursor-like 体验下用户看到长时间空白。设 0 或负值禁用。
    """
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    loop = asyncio.get_event_loop()
    last_output_at = loop.time()

    async def _drain(stream: asyncio.StreamReader | None, sink: list[str]) -> None:
        nonlocal last_output_at
        if stream is None:
            return
        while True:
            raw = await stream.readline()
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            sink.append(text)
            last_output_at = loop.time()
            if text.strip():
                try:
                    await log(text)
                except Exception:
                    pass

    async def _heartbeat() -> None:
        if heartbeat_seconds <= 0:
            return
        started = loop.time()
        while True:
            await asyncio.sleep(heartbeat_seconds)
            now = loop.time()
            silent = now - last_output_at
            total = int(now - started)
            if silent >= heartbeat_seconds:
                try:
                    await log(f"⏳ runner 静默 {int(silent)}s（累计 {total}s）...")
                except Exception:
                    pass

    async def _runner() -> None:
        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            await asyncio.gather(
                _drain(proc.stdout, stdout_lines),
                _drain(proc.stderr, stderr_lines),
            )
            await proc.wait()
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except BaseException:
                pass

    if timeout is None:
        await _runner()
    else:
        await asyncio.wait_for(_runner(), timeout=timeout)

    return "\n".join(stdout_lines), "\n".join(stderr_lines)
