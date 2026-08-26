"""alibaba/open-code-review 适配（§9）。

实测确定的配置（§9.11）：
    端点     DashScope（`json_schema` 可用，不会像 DeepSeek 直连那样静默 400）
    过滤     `--no-filter` —— 内置过滤 85k token/2.5min，自建的 ~1k/条，17 倍差
    背景     `--background-file` 喂需求原文 + 澄清 + 契约，才能审「有没有做到需求」

两条实测得出的硬约束写在代码里：
  1. `retry_report` 无失败时**整个 key 缺失** → 缺失=零失败，不是未知
  2. 0 条发现 ≠ 代码干净 → 同一 diff 三次跑出 2/0/0，调用方不得据此跳过人工审核
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from vplatform.review.adapter import (
    AXIS_DEFECT,
    Finding,
    ReviewError,
    ReviewNotInstalled,
    ReviewResult,
    build_background,   # 重新导出：调用方习惯从这里拿
)

__all__ = ["OcrReviewAdapter", "parse_ocr_json", "build_background"]

logger = logging.getLogger(__name__)


class OcrReviewAdapter:
    def __init__(self, *, binary: str = "ocr", concurrency: int = 4,
                 use_builtin_filter: bool = False, env: dict[str, str] | None = None,
                 timeout: float = 1800):
        self.bin = binary
        self.concurrency = concurrency
        self.use_builtin_filter = use_builtin_filter
        self.env = env or {}
        self.timeout = timeout

    def _argv(self, *, repo_path: str, base: str, head: str,
              bg_file: str | None, rules_path: str | None, token_budget: int) -> list[str]:
        argv = [self.bin, "review", "--repo", repo_path,
                "--from", base, "--to", head,
                "--format", "json", "--audience", "agent",
                "--concurrency", str(self.concurrency),
                "--max-tokens-budget", str(token_budget)]
        if not self.use_builtin_filter:
            argv.append("--no-filter")
        if bg_file:
            argv += ["--background-file", bg_file]
        if rules_path:
            argv += ["--rule", rules_path]
        return argv

    async def review(self, *, repo_path: str, base: str, head: str,
                     background: str = "", rules_path: str | None = None,
                     token_budget: int = 200_000) -> ReviewResult:
        import os

        bg_path = None
        tmp = None
        if background:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                              encoding="utf-8")
            tmp.write(background)
            tmp.close()
            bg_path = tmp.name
        try:
            argv = self._argv(repo_path=repo_path, base=base, head=head,
                              bg_file=bg_path, rules_path=rules_path,
                              token_budget=token_budget)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv, env={**os.environ, **self.env},
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                # **没装就明说，不要抛 FileNotFoundError。**
                # 裸的 `No such file or directory: 'ocr'` 会让整条需求
                # 判失败，而报错跟「AI 复核」三个字毫无关系 ——
                # 排查的人得翻栈才知道是少了个命令行工具。
                # 跟浏览器自检同样的口径：探不到就如实跳过。
                raise ReviewNotInstalled(
                    f"没找到复核工具 `{argv[0]}`。装了它这一环才会真跑："
                    f"参见 alibaba/open-code-review。") from exc
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                proc.kill(); await proc.wait()
                raise ReviewError(f"ocr 超时（{self.timeout}s）") from exc
        finally:
            if bg_path:
                Path(bg_path).unlink(missing_ok=True)

        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")
        if proc.returncode != 0 and not stdout.strip():
            raise ReviewError(f"ocr 非 0 退出 (rc={proc.returncode})\n{stderr[-2000:]}")
        try:
            return parse_ocr_json(stdout)
        except ReviewError as exc:
            # 解析失败时**必须把 stderr 带出来** —— 真正的原因（鉴权失败、
            # 配额耗尽）都在那里，只看 stdout 前 300 字什么也查不出来。
            raise ReviewError(f"{exc}\n--- ocr stderr ---\n{stderr[-1500:]}") from exc


def parse_ocr_json(raw: str) -> ReviewResult:
    """解析 `ocr review --format json` 的输出。

    **`retry_report` 可能整个缺失** —— 无失败时 ocr 不输出这个键。
    缺失要解释成「零失败」而不是「未知」，否则每次成功都会被误报成降级。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"ocr 输出不是合法 JSON: {raw[:300]}") from exc

    summary = data.get("summary") or {}
    findings = [
        Finding(
            axis=AXIS_DEFECT,
            severity=(c.get("severity") or "medium").lower(),
            category=c.get("category") or "",
            path=c.get("path") or "",
            start_line=int(c.get("start_line") or 0),
            end_line=int(c.get("end_line") or 0),
            claim=(c.get("content") or "").strip(),
            existing_code=c.get("existing_code") or "",
            suggestion_code=c.get("suggestion_code") or "",
        )
        for c in data.get("comments") or []
    ]
    return ReviewResult(
        findings=findings,
        tokens=int(summary.get("total_tokens") or 0),
        elapsed=str(summary.get("elapsed") or ""),
        files_reviewed=int(summary.get("files_reviewed") or 0),
        session_id=str(data.get("session_id") or ""),
        failed_requests=int((data.get("retry_report") or {}).get("failed_requests", 0)),
        raw=data,
    )
