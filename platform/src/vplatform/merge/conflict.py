"""三档递进的冲突处理（§12）。

    ① git 三方合并        —— 不相交的行级改动，自动解
    ② mergiraf 结构化合并  —— tree-sitter 语法树合并。确定性、不调模型、不会瞎编，
                             专治 import 顺序 / JSX 属性顺序这类噪音冲突
    ③ AI 解冲突           —— 剩下的真语义冲突。**必须携带原会话**：
                             它知道自己当初为什么这么改

注意 ③ 与 §9 的 reviewer 正好相反 —— reviewer 必须是全新会话（避免自评偏差），
解冲突必须带原会话（需要知道意图）。这个区分不能搞混。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Rung:
    stage: str
    ok: bool
    detail: str = ""
    resolved: int = 0


@dataclass
class LadderResult:
    rungs: list[Rung] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)   # 仍冲突的文件
    # rebase 压根没启动（分支不存在、工作区脏、已有 rebase 在进行中…）。
    # 这种情况**没有冲突文件**，但绝不是"已解决"。
    aborted_reason: str = ""

    @property
    def resolved(self) -> bool:
        # 之前只看 `not self.remaining` —— 于是 rebase 因为 "invalid upstream"
        # 之类原因失败时（不产生任何冲突文件），resolved 会返回 True，
        # 合并队列会把这条标成 merged。
        return not self.remaining and not self.aborted_reason

    def as_json(self) -> list[dict]:
        return [{"stage": r.stage, "ok": r.ok, "detail": r.detail,
                 "resolved": r.resolved} for r in self.rungs]


async def _git(repo: str | Path, *args: str, timeout: float = 300):  # noqa: D401
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(repo),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def conflicted_files(repo: str | Path) -> list[str]:
    rc, out, _ = await _git(repo, "diff", "--name-only", "--diff-filter=U")
    return [ln.strip() for ln in out.splitlines() if ln.strip()] if rc == 0 else []


class ConflictLadder:
    def __init__(self, *, mergiraf_bin: str = "mergiraf",
                 ai_resolver=None):
        self.mergiraf = mergiraf_bin
        self.ai_resolver = ai_resolver     # async (repo, files, session_id) -> list[str] 未解决的

    def _has_mergiraf(self) -> bool:
        return shutil.which(self.mergiraf) is not None

    async def rebase(self, repo: str | Path, *, onto: str, branch: str) -> LadderResult:
        """rebase 到最新 target，然后逐档处理冲突。"""
        res = LadderResult()
        await _git(repo, "checkout", branch)
        rc, out, err = await _git(repo, "rebase", onto, timeout=600)
        if rc == 0:
            res.rungs.append(Rung("git", True, "无冲突", 0))
            return res

        files = await conflicted_files(repo)
        if not files:
            # 非 0 退出但没有冲突文件 = rebase 根本没启动。
            # 常见原因：onto 分支不存在、工作区有未提交改动、已有 rebase 在进行中。
            reason = (err or out).strip()[:300] or f"rebase 非 0 退出 (rc={rc})"
            res.aborted_reason = reason
            res.rungs.append(Rung("git", False, f"rebase 未能启动：{reason}", 0))
            await _git(repo, "rebase", "--abort", timeout=120)
            return res

        res.rungs.append(Rung("git", False, f"{len(files)} 处冲突 git 无法自动解", 0))
        res.remaining = files
        return res

    async def run_mergiraf(self, repo: str | Path, files: list[str]) -> Rung:
        """第二档：结构化三方合并。

        **不装 mergiraf 不是错误** —— 这一档是优化，跳过后直接进 AI 档。
        但要如实记录跳过，别让人以为它跑过了。
        """
        if not files:
            return Rung("mergiraf", True, "无需处理", 0)
        if not self._has_mergiraf():
            return Rung("mergiraf", False, f"未安装 {self.mergiraf}，跳过（直接进 AI 档）", 0)

        solved = 0
        for f in list(files):
            proc = await asyncio.create_subprocess_exec(
                self.mergiraf, "solve", f, cwd=str(repo),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
            if proc.returncode == 0 and not (await _has_markers(Path(repo) / f)):
                await _git(repo, "add", f)
                files.remove(f)
                solved += 1
        return Rung("mergiraf", True, f"结构化合并解决 {solved} 处", solved)

    async def run_ai(self, repo: str | Path, files: list[str], *,
                     session_id: str | None) -> Rung:
        """第三档：AI 解冲突，**携带原会话**。"""
        if not files:
            return Rung("ai", True, "无需处理", 0)
        if self.ai_resolver is None:
            return Rung("ai", False, "未配置 AI 解冲突器", 0)
        before = len(files)
        touched = list(files)
        remaining = await self.ai_resolver(repo, files, session_id)

        # **AI 解完的文件必须 git add**，否则 `rebase --continue` 必然报
        # "needs merge"，第三档永远无法收尾。mergiraf 档自己 add 了，
        # AI 档之前漏了这一步。
        # 同时校验残留标记 —— AI 声称解完但留着 <<<<<<< 的文件不能算解决。
        still: list[str] = list(remaining)
        for f in touched:
            if f in still:
                continue
            if await _has_markers(Path(repo) / f):
                still.append(f)          # AI 说解了，但标记还在
                continue
            await _git(repo, "add", f, timeout=120)
        files[:] = still
        solved = before - len(still)
        return Rung("ai", not still,
                    f"AI 解决 {solved}/{before} 处"
                    + (f"（会话 {session_id}）" if session_id else ""),
                    solved)

    async def resolve(self, repo: str | Path, *, onto: str, branch: str,
                      session_id: str | None = None) -> LadderResult:
        """跑完整三档。"""
        res = await self.rebase(repo, onto=onto, branch=branch)
        if res.aborted_reason or res.resolved:
            return res
        files = res.remaining
        res.rungs.append(await self.run_mergiraf(repo, files))
        if files:
            res.rungs.append(await self.run_ai(repo, files, session_id=session_id))
        res.remaining = files
        if res.resolved:
            rc, _, err = await _git(repo, "rebase", "--continue", timeout=300)
            if rc != 0:
                res.remaining = await conflicted_files(repo)
                res.rungs.append(Rung("rebase-continue", False, err.strip()[:300]))
        else:
            # **必须 abort**。之前失败分支只记一条 rung 就返回，仓库停在
            # rebase-merge 状态；下一条合并任务对同一个仓再 rebase 会撞上
            # "there is already a rebase-merge directory"，该仓的队列从此卡死。
            await _git(repo, "rebase", "--abort", timeout=120)
        return res


async def _has_markers(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "<<<<<<<" in text and ">>>>>>>" in text
