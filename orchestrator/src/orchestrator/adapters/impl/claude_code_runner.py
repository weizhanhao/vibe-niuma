"""ClaudeCodeDevRunner —— 调 claude CLI 的非交互模式（`claude -p`），让它在
分支上按 DevContext 改代码。跑完用 _dev_runner_common 判定 changed + commit。

环境变量：
- ANTHROPIC_BASE_URL  —— 指向 Anthropic-compatible 代理（DeepSeek / 通义千问）
- ANTHROPIC_API_KEY   —— settings.llm_api_key

非交互模式约定：用 `claude -p <prompt>`。如果将来 claude CLI 改了非交互参数，
只需改 _build_argv。
"""
from __future__ import annotations

import asyncio
import os

from orchestrator.adapters.impl._dev_runner_common import (
    collect_log,
    make_run_result,
    stream_subprocess,
)
from orchestrator.adapters.types import DevContext, RunResult
from orchestrator.config import settings


class ClaudeCodeDevRunner:
    """实现 DevRunnerAdapter Protocol。"""

    def __init__(
        self,
        *,
        cli: str = "claude",
        timeout_seconds: int | None = None,
        anthropic_base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._cli = cli
        self._timeout = timeout_seconds or settings.dev_runner_timeout_seconds
        self._base_url = anthropic_base_url or settings.anthropic_base_url
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
                    f"claude CLI 超时（{self._timeout}s）"
                ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(f"找不到 {self._cli} CLI: {exc}") from exc

        log = collect_log(stdout_text, stderr_text)
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI 非 0 退出 (rc={proc.returncode})\n{log}"
            )
        return make_run_result(repo_path, branch, log, message=f"cr: {ctx.brief.original_text[:60]}")

    def _build_argv(self, prompt: str) -> tuple[list[str], dict]:
        argv = [self._cli, "-p", prompt, "--model", self._model]
        env = {
            **os.environ,
            "ANTHROPIC_BASE_URL": self._base_url,
            "ANTHROPIC_API_KEY": self._api_key,
        }
        return argv, env


def build_prompt(ctx: DevContext) -> str:
    """把 DevContext 拼成给 claude 的非交互 prompt。

    Plan 10 Task 6：按 ctx.mode 分支：
      - 'new' (默认) → 完整 prompt（含入口文件 dump）
      - 'refine' → 续改 ctx.base_branch；不 dump 入口文件；提示用 git diff 看上轮改动
    """
    if getattr(ctx, "mode", "new") == "refine":
        return _build_refine_prompt(ctx)
    return _build_new_prompt(ctx)


def _build_new_prompt(ctx: DevContext) -> str:
    """new_cr 路径：现状（不破）。"""
    parts: list[str] = []
    parts.append("你是一个改代码的 AI 工程师。请按下面的业务需求修改本仓库的代码：")
    parts.append("")
    parts.append("【业务需求】")
    parts.append(ctx.brief.original_text or "")
    if ctx.brief.clarifications:
        parts.append("")
        parts.append("【澄清问答】")
        for c in ctx.brief.clarifications:
            parts.append(f"- Q: {c.get('question')}")
            parts.append(f"  A: {c.get('answer')}")
    if ctx.brief.selected_mockup:
        parts.append("")
        parts.append("【业务员选中的视觉方向（HTML 锚点，不是直接抄）】")
        parts.append(ctx.brief.selected_mockup.html or "")
    parts.append("")
    parts.append("【入口源文件（请在这些文件 / 它们附近改）】")
    for path in ctx.locate_result.entry_files:
        parts.append(f"- {path}")
    if ctx.entry_file_contents:
        parts.append("")
        parts.append("【入口文件当前内容】")
        for path, content in ctx.entry_file_contents.items():
            parts.append(f"--- {path} ---")
            parts.append(content)
            parts.append("")
    parts.append("")
    parts.append(f"【框选区域】坐标 {ctx.box_coords} —— 业务员框的就是这一块。")
    parts.append("（截图已经一并提供，参见对话上下文。）")
    parts.append("")
    parts.append("【要求】")
    parts.append("- 在当前 git 分支上修改文件，保持现有风格 / 命名 / 结构。")
    parts.append("- 改完自行用 `git add -A && git commit -m '...'` 提交。")
    parts.append("- 如果有构建/类型错误，自己修到通过。")
    return "\n".join(parts)


def _build_refine_prompt(ctx: DevContext) -> str:
    """refine_cr 路径：业务员追加反馈（「字号大一点」），续改 base_branch。

    跟 new_cr 不同点：
      - 不 dump 入口文件全内容（节省 token；runner 用 git diff 看上轮改动）
      - 明确告诉 runner 「你已经在分支 X 上做过一轮改动」+ 「在当前分支续 commit，不新建分支」
      - 带 chat_history 让 runner 看到业务员之前怎么说的
    """
    parts: list[str] = []
    base = ctx.base_branch or "（未知 base）"
    parts.append(
        f"你是一个改代码的 AI 工程师。你已经在分支 {base} 上做过一轮改动，"
        f"现在业务员追加了反馈，请在**当前分支**（已存在）上续改、续 commit："
    )
    parts.append("")
    parts.append("【业务员追加的反馈（本轮需求）】")
    parts.append(ctx.brief.original_text or "")

    if ctx.chat_history:
        parts.append("")
        parts.append("【之前的对话历史（按时间顺序）】")
        for m in ctx.chat_history:
            t = m.get("type", "?")
            c = (m.get("content") or "").strip()
            if c:
                parts.append(f"[{t}] {c}")

    if ctx.brief.clarifications:
        parts.append("")
        parts.append("【本轮澄清问答】")
        for c in ctx.brief.clarifications:
            parts.append(f"- Q: {c.get('question')}")
            parts.append(f"  A: {c.get('answer')}")

    parts.append("")
    parts.append("【相关入口文件（之前已经改过；这次按业务员追加的反馈微调）】")
    for path in ctx.locate_result.entry_files:
        parts.append(f"- {path}")
    parts.append("")
    parts.append(
        "用 `git diff` 看上一轮你自己改了什么，然后按业务员的追加反馈"
        "在**已存在的当前分支**上继续 commit。"
    )

    if ctx.box_coords:
        parts.append("")
        parts.append(f"【本轮框选区域】坐标 {ctx.box_coords}")

    parts.append("")
    parts.append("【要求】")
    parts.append("- **不要新建 git 分支**——已经在 base branch 上了，直接 `git add -A && git commit -m '...'` 续 commit。")
    parts.append("- 保持现有风格 / 命名 / 结构。")
    parts.append("- 如果有构建/类型错误，自己修到通过。")
    return "\n".join(parts)
