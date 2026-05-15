"""OpenCodeDevRunner —— 调 opencode CLI 的非交互模式。

opencode 原生多 provider，不需要 Anthropic-compatible 代理。模型 + key 直接走
opencode 的 OPENCODE_MODEL / 对应 provider env（如 OPENAI_API_KEY / DEEPSEEK_API_KEY）。

非交互调用约定：`opencode run "<prompt>"`。如 CLI 改了非交互参数，只需改 _build_argv。
"""
from __future__ import annotations

import asyncio
import os

from orchestrator.adapters.impl._dev_runner_common import (
    collect_log,
    make_run_result,
    stream_subprocess,
)
from orchestrator.adapters.impl.claude_code_runner import build_prompt
from orchestrator.adapters.types import DevContext, RunResult
from orchestrator.config import settings


class OpenCodeDevRunner:
    """实现 DevRunnerAdapter Protocol。"""

    def __init__(
        self,
        *,
        cli: str = "opencode",
        timeout_seconds: int | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._cli = cli
        self._timeout = timeout_seconds or settings.dev_runner_timeout_seconds
        self._api_key = api_key if api_key is not None else settings.llm_api_key
        self._model = model or settings.dev_model

    async def run(self, repo_path: str, branch: str, ctx: DevContext) -> RunResult:
        prompt = build_prompt(ctx)
        argv, env = self._build_argv(prompt)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                # Phase F：stream stdout/stderr 而不是 communicate()；
                # 每读到一行就 await ctx.log(line)，让扩展实时看到进度。
                stdout_text, stderr_text = await stream_subprocess(
                    proc, ctx.log, timeout=self._timeout,
                )
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise RuntimeError(
                    f"opencode CLI 超时（{self._timeout}s）"
                ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(f"找不到 {self._cli} CLI: {exc}") from exc

        log = collect_log(stdout_text, stderr_text)
        if proc.returncode != 0:
            raise RuntimeError(
                f"opencode CLI 非 0 退出 (rc={proc.returncode})\n{log}"
            )
        return make_run_result(repo_path, branch, log, message=f"cr: {ctx.brief.original_text[:60]}")

    def _build_argv(self, prompt: str) -> tuple[list[str], dict]:
        argv = [self._cli, "run", prompt, "--model", self._model]
        # opencode 直连 provider（不经 LiteLLM），需要真实 provider key。
        # 优先用 os.environ 已有值（systemd EnvironmentFile 注入），没有再
        # 回退 self._api_key（fake 测试 / 单 provider 场景）。
        def _pick(name: str) -> str:
            return os.environ.get(name) or self._api_key
        env = {
            **os.environ,
            "OPENAI_API_KEY": _pick("OPENAI_API_KEY"),
            "DEEPSEEK_API_KEY": _pick("DEEPSEEK_API_KEY"),
            "ANTHROPIC_API_KEY": _pick("ANTHROPIC_API_KEY"),
            "DASHSCOPE_API_KEY": _pick("DASHSCOPE_API_KEY"),
        }
        return argv, env
