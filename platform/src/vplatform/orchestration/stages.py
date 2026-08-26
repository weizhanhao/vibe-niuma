"""各环节的真实执行体（§7.4 的 stage → 能力落地）。

与 handlers.py 的分工：
    handlers.py  调度 —— 谁先谁后、何时挂起等人、怎么幂等重放
    stages.py    执行 —— 这个环节具体做什么

依赖仍然**全部注入**（Capabilities），这里不 import 任何具体实现，
接缝守卫盯着（scripts/check_seams.py）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from vplatform.core.models import (
    AgentSession as AgentSessionRow,
    Finding as FindingRow, Message as MessageRow, PortLease, Project, ProjectRepo,
    Requirement, Run, Task, TaskTouch, Workspace as WorkspaceRow,
)
from vplatform.core.events import get_bus
from vplatform.orchestration.dag import Stage

logger = logging.getLogger(__name__)


# ── to-tickets 产出解析（§14.5 ②：orchestrator 读 local files 入库）────
@dataclass
class Ticket:
    key: str = ""
    title: str = ""
    delivers: str = ""
    blocked_by: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)
    touches: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    sequence: str | None = None


_FIELD = re.compile(r"^\*\*(?P<k>[^:*]+):?\*\*[:：]?\s*(?P<v>.*)$")
_HEAD_NUM = re.compile(r"^#+\s*(?P<n>[\w.-]+)\s*[:：]\s*(?P<t>.+)$")
_HEAD_ANY = re.compile(r"^#\s+(?P<t>.+)$")
# 形如 `backend/engine/x.py` 或 `tests/test_y.py` 的行内代码
_PATHISH = re.compile(r"`([^`\s]+\.(?:py|ts|tsx|js|jsx|go|rs|java|html|css|sql|ya?ml|json|md))`")


def _split_list(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw or raw.lower().startswith(("none", "无")):
        return []
    return [x.strip().strip("`") for x in re.split(r"[,，;；\n]", raw) if x.strip()]


def parse_ticket(text: str) -> Ticket:
    """解析一个 ticket 文件。

    优先认打过 patch 的 to-tickets 模板（`**Touches:**` 等字段）。
    **认不出字段时退回宽松解析** —— agent 不会永远严格照模板走，
    解析器太脆就会把一份好产出整个丢掉（实测：agent 写了一份质量很高的
    markdown 方案，字段名不匹配，直接被判成「未产出 ticket」）。

    宽松模式从标题取 title、从行内代码里的路径取 touches。
    """
    tk = Ticket()
    lines = text.splitlines()

    for ln in lines:
        m = _HEAD_NUM.match(ln.strip())
        if m and m.group("n").lstrip("0").isdigit():
            tk.key = f"T{m.group('n').lstrip('0') or '1'}"
            tk.title = m.group("t").strip()
            break
    if not tk.title:
        for ln in lines:
            m = _HEAD_ANY.match(ln.strip())
            if m:
                tk.title = m.group("t").strip()
                break

    body_key = None
    for ln in lines:
        m = _FIELD.match(ln.strip())
        if m:
            k = m.group("k").strip().lower()
            v = m.group("v").strip()
            body_key = k
            if k.startswith("what to build"):
                tk.delivers = v
            elif k.startswith("blocked by"):
                tk.blocked_by = [x if x.upper().startswith("T")
                                 else f"T{x.lstrip('0') or '1'}" for x in _split_list(v)]
            elif k.startswith("repos"):
                tk.repos = _split_list(v)
            elif k.startswith("touches"):
                tk.touches = _split_list(v)
            elif k.startswith("contracts"):
                tk.contracts = _split_list(v)
            elif k.startswith("sequence"):
                tk.sequence = _norm_sequence(v)
            continue
        stripped = ln.strip().lstrip("-").strip()
        if body_key == "touches" and stripped and not stripped.startswith("**"):
            tk.touches.append(stripped.strip("`"))
        elif body_key == "contracts" and stripped and not stripped.startswith("**"):
            tk.contracts.append(stripped.strip("`"))

    if not tk.touches:
        # 宽松兜底：从行内代码里捞路径（markdown 表格、正文都算）
        tk.touches = list(dict.fromkeys(_PATHISH.findall(text)))

    tk.touches = [t for t in dict.fromkeys(tk.touches)
                  if t and ("/" in t or t.endswith((".py", ".ts", ".tsx", ".js")))]
    tk.contracts = [c for c in dict.fromkeys(tk.contracts) if c]
    return tk


def parse_tickets(dirpath: Path) -> list[Ticket]:
    """读 `.scratch/<req>/issues/NN-slug.md`，按文件名排序（= 依赖顺序）。"""
    if not dirpath.is_dir():
        return []
    out: list[Ticket] = []
    for f in sorted(dirpath.glob("*.md")):
        try:
            tk = parse_ticket(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if tk.title:
            if not tk.key:
                tk.key = f"T{len(out) + 1}"
            out.append(tk)
        else:
            logger.warning("ticket 文件 %s 认不出标题，跳过", f.name)
    return out


_Q_LINE = re.compile(r"^\s*(?:[-*\d.)：:]+\s*)?(.+\?|.+？)\s*$")


# 立需求最多谈几轮。超过就强制出稿。
_MAX_INTAKE_ROUNDS = 3

DRAFT_FORMAT = """
```需求稿
标题: 一句话，动词开头
背景: 为什么要做，现在是怎样的
要做什么:
- ...
验收标准:
- [ ] 可验证的、能一眼判断做没做到的条件
```
"""


async def _port_open(url: str) -> bool:
    """这个地址真的有人监听吗。连不上就是没有。"""
    import contextlib
    from urllib.parse import urlparse

    u = urlparse(url)
    if not u.hostname or not u.port:
        return False
    try:
        fut = asyncio.open_connection(u.hostname, u.port)
        reader, writer = await asyncio.wait_for(fut, timeout=2)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True
    except Exception:  # noqa: BLE001
        return False


def browser_bin(settings=None) -> str:
    """ego lite 的 CLI 在哪。找不到返回空串。

    **不能只查 PATH。** ego lite 把命令装在 `~/.local/bin`，而 worker 是
    后台进程 —— launchd / systemd / nohup 起来的进程拿到的是精简 PATH，
    通常不含它。只查 PATH 的话：终端里 `command -v ego-browser` 有，
    平台却一直报「没装」，排查的人会去怀疑安装本身。
    """
    from shutil import which
    binary = getattr(settings, "ego_browser_bin", None) or "ego-browser"
    found = which(binary)
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / binary
    return str(fallback) if os.access(fallback, os.X_OK) else ""


def browser_available(settings=None) -> bool:
    """宿主上有没有 ego lite 的 CLI。

    **不能假定装了。** ego lite 目前只有 macOS 版，而且是宿主上的桌面应用 ——
    容器里的 agent 够不着。探不到就如实跳过，不能把「没检查」说成「通过」。
    """
    return bool(browser_bin(settings))


# wide refactor 序列只有这三个取值（§8.4）。列宽 16。
_SEQUENCES = ("expand", "migrate", "contract")


def _norm_sequence(raw: str | None) -> str | None:
    """把 ticket 里的 `Sequence:` 规范成三个已知值之一，否则 None。

    **不能把原文直接塞进去。** 这一列是 `String(16)`，而 agent 会往里写
    整句话 —— 实测写过 `n/a（非 wide refactor，单 ticket 直落）`，
    直接 `Data too long for column 'sequence'`，**整个拆解环节炸掉**，
    而报错跟「拆解」两个字毫无关系，排查的人只会看到一条 SQL 异常。
    """
    if not raw:
        return None
    v = raw.split("|")[0].strip().lower()
    for known in _SEQUENCES:
        if v.startswith(known):
            return known
    return None                 # n/a、无、none、一整句话 —— 都不是序列


def parse_draft(text: str) -> dict | None:
    """从 agent 回复里取需求稿。取不到返回 None。

    **取不到就不能当成谈完了。** 硬把整段回复塞进 body 的话，
    提问也会被当成需求稿，人还没回答就被推去确认。
    """
    m = re.search(r"```\s*需求稿\s*\n(.*?)```", text, re.S)
    if not m:
        return None
    block = m.group(1).strip()
    if not block:
        return None
    t = re.search(r"^\s*标题\s*[:：]\s*(.+)$", block, re.M)
    return {"title": (t.group(1).strip() if t else ""), "body": block}


def _extract_questions(text: str) -> list[str]:
    """从 agent 回复里挑出问题行。

    宽松一点：以问号结尾的行就算。太严会把问题漏掉 ——
    漏掉就等于没澄清，用户看到一个空的对话框。

    **长度不能按字符数一刀切。** 之前要求 >5 个字符，中文里
    「要记住吗？」正好 5 个字就被丢掉了 —— 一句完整的中文问题
    比同义英文短得多。改成看有没有实际内容：去掉问号后至少
    3 个字、且含真实文字（不是「1?」这种编号残渣）。
    """
    out: list[str] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.upper().startswith("READY"):
            continue
        m = _Q_LINE.match(ln)
        if not m:
            continue
        q = m.group(1).strip()
        body = q.rstrip("?？").strip()
        if len(body) < 3 or not any(c.isalpha() for c in body):
            continue
        if q not in out:
            out.append(q)
    return out[:3]


def find_tickets(ws, out_dir: str) -> list:
    """在工位根和每个仓库目录下找 ticket。

    opencode 的项目根检测会落到 git 仓那一层，agent 往往把 `.scratch/...`
    写进仓库目录而不是工位根。只查一个地方会把好拆解判成「未产出」。
    """
    seen: list = []
    roots = [Path(ws.root), *(Path(p) for p in (ws.repos or {}).values())]
    for root in roots:
        found = parse_tickets(root / out_dir)
        if found:
            logger.info("在 %s 找到 %d 个 ticket", root / out_dir, len(found))
            seen.extend(found)
    # 去重：同一份可能在两处都被扫到
    uniq, titles = [], set()
    for t in seen:
        if t.title not in titles:
            titles.add(t.title)
            uniq.append(t)
    for i, t in enumerate(uniq, 1):
        t.key = t.key or f"T{i}"
    return uniq


def topo_layers(tasks: list) -> list[list]:
    """按 depends_on 拓扑分层。同层可并发，层间串行。

    有环时把剩余任务放进最后一层（串行跑）而不是死循环 ——
    拆解 agent 偶尔会产出环，不能因此卡死整条需求。
    """
    by_key = {t.key: t for t in tasks}
    done: set[str] = set()
    layers: list[list] = []
    remaining = list(tasks)
    while remaining:
        layer = [t for t in remaining
                 if all(d not in by_key or d in done for d in (t.depends_on or []))]
        if not layer:
            logger.warning("任务依赖成环，剩余 %d 个降级为串行",
                           len(remaining))
            layers.extend([[t] for t in remaining])
            break
        layers.append(layer)
        done.update(t.key for t in layer)
        remaining = [t for t in remaining if t not in layer]
    return layers


# 命令名 → 各栈的实际执行方式。没有对应文件就跳过该仓。
#
# **Python 解释器用 `PY` 占位，运行时解析。**
# 之前写死 `python` —— macOS 和不少 Linux 发行版上只有 `python3`，
# 直接 FileNotFoundError，而且报的是「找不到 python」这种看不出上下文的错。
_PY = "\x00PY\x00"
_COMMANDS: dict[str, list[tuple[str, list[str]]]] = {
    "lint":  [("package.json", ["npm", "run", "--if-present", "lint"]),
              ("pyproject.toml", [_PY, "-m", "ruff", "check", "."])],
    "test":  [("package.json", ["npm", "test", "--if-present"]),
              ("pyproject.toml", [_PY, "-m", "pytest", "-q"]),
              ("requirements.txt", [_PY, "-m", "pytest", "-q"]),
              ("pytest.ini", [_PY, "-m", "pytest", "-q"]),
              ("tox.ini", [_PY, "-m", "pytest", "-q"])],
    "build": [("package.json", ["npm", "run", "--if-present", "build"]),
              ("pyproject.toml", [_PY, "-m", "compileall", "-q", "."])],
    "e2e":   [("package.json", ["npm", "run", "--if-present", "e2e"])],
}


def python_bin() -> str:
    """本机可用的 Python 解释器名。

    容器里是 `python3`，宿主上可能只有 `python3` 也可能两个都有。
    写死 `python` 会在 macOS 上直接 FileNotFoundError。
    """
    import shutil
    for cand in ("python3", "python"):
        if shutil.which(cand):
            return cand
    return "python3"


def resolve_command(name: str, repo_dir: Path) -> list[str] | None:
    """把抽象命令名解析成这个仓真正能跑的 argv。解析不出返回 None（跳过）。"""
    for marker, argv in _COMMANDS.get(name, []):
        if (repo_dir / marker).exists():
            py = python_bin()
            return [py if a == _PY else a for a in argv]
    return None


@dataclass
class StageOutcome:
    ok: bool
    detail: str = ""
    data: dict | None = None

    def as_dict(self) -> dict:
        out = {"ok": self.ok, "detail": self.detail}
        if self.data:
            out.update(self.data)
        return out


class StageRunner:
    """把一个环节跑起来。caps 里缺什么就跳过什么，并**明说缺了什么**。"""

    def __init__(self, caps, session):
        self.caps = caps
        self.s = session

    # ── 澄清（人机对话）────────────────────────────────────────
    async def clarify(self, stage: Stage, req: Requirement) -> StageOutcome:
        """让 agent 看需求 + 仓库，把话问清楚。**问完挂起等人回答。**

        之前这个环节在 DISPATCH 里没有条目 → 直接 `{"ok": True}` 空转，
        用户提完需求一句话都插不进去。而「真多轮澄清」是这套东西的核心 ——
        业务员表达不清楚，后面拆解和实现全是白干。

        走法：
          第一次进来   agent 提问 → 落 Message → park 等人回答
          人回答后再进 带上完整对话重新判断 → 还有问题继续问 / 够了就放行
          人说「够了」 直接放行，拿现有信息开工
        """
        msgs = self._messages(req)
        pending = [m for m in msgs if m.role == "agent" and m.awaiting_answer]

        # 人已经说「够了直接干」
        if any(m.role == "user" and m.body.strip().startswith("✓") for m in msgs):
            return StageOutcome(True, "业务员选择跳过澄清，按现有信息开工",
                                {"rounds": len([m for m in msgs if m.role == "agent"]),
                                 "skipped_by_user": True})

        if pending:
            # 还有没答的问题 —— 继续等
            return StageOutcome(True, "等待业务员回答", {"awaiting": len(pending)})

        if self.caps.agent is None or self.caps.workspace is None:
            return StageOutcome(True, "agent/workspace 未注入，跳过澄清",
                                {"skipped": True})

        rounds = len([m for m in msgs if m.role == "agent"])
        if rounds >= 3:
            # 别问起来没完 —— 三轮还问不清就直接开工，让实现阶段去暴露问题
            return StageOutcome(True, f"已澄清 {rounds} 轮，按现有信息开工",
                                {"rounds": rounds})

        from vplatform.orchestration.handlers import skill_prompt

        specs = self._repo_specs(req.project_id)
        ws = await self.caps.workspace.acquire(
            project_id=req.project_id, run_id=f"clarify-{req.id[:12]}",
            branch=f"clarify/{req.seq}",
            base_branch=self.target_branch(req.project_id), repos=specs)
        try:
            prompt = skill_prompt(stage, context=(
                f"业务员提了这条需求：\n\n{req.title}\n{req.body}\n"
                + self.repo_map(ws) + "\n"
                + self._transcript(msgs)
                + "\n先看一眼代码库，然后判断：\n"
                "- 信息够不够动手？够就只回一行 `READY`，不要多问。\n"
                "- 不够就提**最多 3 个**真正影响实现方式的问题，一行一个，"
                "不要问业务员答不上来的技术细节。"))
            reply = await self._talk(req=req, purpose="clarify", prompt=prompt,
                                     cwd=self.agent_cwd(ws))
        finally:
            await self._release(ws)

        text = (reply.text or "").strip()
        questions = _extract_questions(text)
        if not questions or "READY" in text.upper()[:200]:
            self._say(req, "agent", "信息够了，开始拆解。", stage="clarify",
                      trace=self.take_trace())
            return StageOutcome(True, "信息充分，无需澄清", {"rounds": rounds})

        self._say(req, "agent", "\n".join(questions), stage="clarify",
                  awaiting=True, trace=self.take_trace())
        return StageOutcome(True, f"提了 {len(questions)} 个问题，等业务员回答",
                            {"questions": len(questions), "awaiting": True})

    # ── 立需求（进流程之前的那段对话）──────────────────────────
    async def refine_draft(self, req: Requirement) -> dict:
        """跟提需求的人把需求聊成型。**这一段在流水线之外。**

        之前「提需求」是一个表单：填完标题正文就直接进 triage 往下跑。
        可是业务员坐下来的时候脑子里往往只有一句「导出太难用了」——
        表单逼他一次性写清楚，写不清楚就带着含糊往下走，
        后面拆解、实现全按错的理解做完，到人工审核才发现方向不对。

        改成先谈：AI 读代码库、提问、给出一份需求稿，人看着改，
        点「确认」才进流程。草稿不占并行工位，也不上看板。
        """
        msgs = self._messages(req)
        if any(m.role == "agent" and m.awaiting_answer for m in msgs):
            return {"awaiting": True}

        if self.caps.agent is None or self.caps.workspace is None:
            # 没有 agent 就别装作谈过 —— 直接把原话当需求稿，让人自己改
            self._say(req, "system",
                      "平台没有配 agent，跳过对话。你可以直接编辑需求稿后提交。",
                      stage="intake")
            return {"skipped": True, "ready": True}

        rounds = len([m for m in msgs if m.role == "agent"])
        # 「✓ 够了直接干」= 别再问了，现在就出稿。
        # **之前只有 clarify 认这个标记**，立需求页上那个按钮点了等于没点，
        # AI 接着问下一轮 —— 业务员被困在问答里出不来。
        if any(m.role == "user" and m.body.strip().startswith("✓") for m in msgs):
            rounds = max(rounds, _MAX_INTAKE_ROUNDS)
        specs = self._repo_specs(req.project_id)
        ws = await self.caps.workspace.acquire(
            project_id=req.project_id, run_id=f"intake-{req.id[:12]}",
            branch=f"intake/{req.seq}",
            base_branch=self.target_branch(req.project_id), repos=specs)
        try:
            reply = await self._talk(
                req=req, purpose="intake", cwd=self.agent_cwd(ws),
                prompt=self._intake_prompt(req, msgs, rounds) + self.repo_map(ws))
        finally:
            await self._release(ws)

        text = (reply.text or "").strip()
        draft = parse_draft(text)
        if draft:
            req.title = draft["title"][:300] or req.title
            req.body = draft["body"]
            self.s.flush()
            self._say(req, "agent", text, stage="intake", trace=self.take_trace())
            return {"ready": True, "rounds": rounds}

        questions = _extract_questions(text)
        if not questions:
            # 既没给需求稿也没提问 —— 别假装谈成了，把原话摆出来让人接话
            self._say(req, "agent", text or "（没有输出）", stage="intake",
                      trace=self.take_trace())
            return {"rounds": rounds}
        self._say(req, "agent", "\n".join(questions), stage="intake", awaiting=True,
                  trace=self.take_trace())
        return {"awaiting": True, "questions": len(questions)}

    def _intake_prompt(self, req: Requirement, msgs: list, rounds: int) -> str:
        from vplatform.orchestration.handlers import _LANG
        repos = self.s.execute(
            select(ProjectRepo.name).where(ProjectRepo.project_id == req.project_id)
        ).scalars().all()
        head = (
            _LANG + "\n\n"
            f"有人想提一条需求，原话是：\n\n{req.body or req.title}\n\n"
            f"这个空间里有这些仓：{', '.join(repos) or '（还没绑仓）'}\n\n"
            + self._transcript(msgs))
        if rounds >= _MAX_INTAKE_ROUNDS:
            # 别聊起来没完 —— 谈够了就先出稿，让人改比让人一直答问题强
            return head + (
                "已经聊了几轮了，**现在必须出需求稿**，不要再提问。\n"
                + DRAFT_FORMAT)
        return head + (
            "你的任务是把这条需求谈清楚，然后写成一份需求稿。\n"
            "先看一眼相关代码，判断：\n"
            "- 还有影响做法的地方没问清楚 → 提**最多 3 个**问题，一行一个，"
            "问业务上的选择，不要问技术细节。\n"
            "- 已经够清楚 → 直接输出需求稿，格式如下：\n"
            + DRAFT_FORMAT)

    # ── 对话工具 ───────────────────────────────────────────────
    def _messages(self, req: Requirement) -> list:
        return list(self.s.execute(
            select(MessageRow).where(MessageRow.requirement_id == req.id)
            .order_by(MessageRow.created_at)
        ).scalars())

    def _say(self, req: Requirement, role: str, body: str, *, stage: str = "",
             author: str = "", awaiting: bool = False,
             trace: list | None = None) -> None:
        """记一条消息。

        `trace=True` 语义的那个参数收的是**刚才那次 agent 运行的思考过程**：
        实时流只在内存里、进程重启就没了，落一份到消息上，刷新页面还能展开看。
        """
        self.s.add(MessageRow(project_id=req.project_id, requirement_id=req.id,
                              role=role, author=author or role, body=body,
                              stage=stage, awaiting_answer=awaiting,
                              trace=list(trace or [])))
        self.s.flush()

    def take_trace(self) -> list:
        """取走上一次 `_talk()` 的思考过程。取过就清掉 —— 不清的话
        下一条消息会把上一次的思考再贴一遍。"""
        t = getattr(self, "_last_trace", None) or []
        self._last_trace = []
        return t

    def _transcript(self, msgs: list) -> str:
        if not msgs:
            return ""
        lines = ["已有对话："]
        for m in msgs:
            who = {"user": "业务员", "agent": "AI", "system": "平台"}.get(m.role, m.role)
            lines.append(f"[{who}] {m.body.strip()[:500]}")
        return "\n".join(lines) + "\n"

    # ── 拆解 ───────────────────────────────────────────────────
    async def decompose(self, stage: Stage, req: Requirement) -> StageOutcome:
        """让 agent 用 to-tickets skill 拆需求，**读回产出落库**。

        之前这里只 `logger.info(prompt)` 就 return "已提交拆解" —— prompt 从未
        离开进程。结果注入 agent 后 Task 表是空的，下一环节报「拆解没产出」，
        排查的人会去查 skill、查 prompt，唯独想不到那行 return 里根本没有 IO。
        """
        repos = self.s.execute(
            select(ProjectRepo.name).where(ProjectRepo.project_id == req.project_id)
        ).scalars().all()

        if self.caps.agent is None or self.caps.workspace is None:
            t = Task(project_id=req.project_id, requirement_id=req.id, key="T1",
                     title=req.title, repo_names=list(repos), state="pending")
            self.s.add(t)
            self.s.flush()
            return StageOutcome(True, "agent/workspace 未注入 → 降级为单任务串行",
                                {"tasks": 1, "degraded": True})

        from vplatform.orchestration.handlers import skill_prompt

        specs = self._repo_specs(req.project_id)
        ws = await self.caps.workspace.acquire(
            project_id=req.project_id, run_id=f"plan-{req.id[:12]}",
            branch=f"plan/{req.seq}",
            base_branch=self.target_branch(req.project_id), repos=specs)
        try:
            out_dir = f".scratch/{req.id}/issues"
            prompt = skill_prompt(stage, context=(
                f"需求：{req.title}\n\n{req.body}\n\n"
                f"本空间的仓：{', '.join(repos)}\n"
                + self.repo_map(ws)
                + f"**用 local files 模式**，把每个 ticket 写成一个文件到 `{out_dir}/NN-slug.md`，"
                f"字段用 patch 后的模板（Blocked by / Repos / Touches / Contracts / Sequence）。"
            ))
            reply = await self._talk(req=req, purpose="plan", prompt=prompt,
                                     cwd=self.agent_cwd(ws))
            # **工位根和每个仓库目录都要找。**
            # opencode 的项目根检测会落到 git 仓那一层，所以 agent 往往把
            # `.scratch/...` 写进仓库目录而不是工位根 —— 只查工位根会
            # 判成「未产出 ticket」，把一份好拆解整个丢掉（实测踩过）。
            tickets = find_tickets(ws, out_dir)
            if not tickets:
                logger.warning("没找到 ticket。agent 回复：%s",
                               (reply.text or "")[:400])
        finally:
            await self._release(ws)

        if not tickets:
            # **拆解没产出不能当成功** —— 降级为单任务并标明，否则下游会以为
            # 并行度 1 是 AI 的判断
            t = Task(project_id=req.project_id, requirement_id=req.id, key="T1",
                     title=req.title, repo_names=list(repos), state="pending")
            self.s.add(t)
            self.s.flush()
            return StageOutcome(True, "agent 未产出 ticket → 降级为单任务",
                                {"tasks": 1, "degraded": True,
                                 "agent_said": (reply.text or "")[:600]})

        contracts: list[str] = []
        for i, tk in enumerate(tickets, start=1):
            task = Task(project_id=req.project_id, requirement_id=req.id,
                        key=tk.key or f"T{i}", title=tk.title,
                        delivers=tk.delivers,
                        repo_names=[r for r in tk.repos if r in repos] or list(repos),
                        depends_on=tk.blocked_by, sequence=tk.sequence,
                        state="pending")
            self.s.add(task)
            self.s.flush()
            for path in tk.touches:
                self.s.add(TaskTouch(project_id=req.project_id, task_id=task.id,
                                     path=path,
                                     repo_name=task.repo_names[0] if task.repo_names else ""))
            contracts.extend(tk.contracts)
        if contracts:
            req.contracts = sorted(set(contracts))
        if any(t.sequence for t in tickets):
            req.sequence_kind = "expand"     # wide refactor 序列（§8.4）
        self.s.flush()
        return StageOutcome(True, f"拆出 {len(tickets)} 个任务",
                            {"tasks": len(tickets), "contracts": len(contracts)})

    # ── 实现（真并行）──────────────────────────────────────────
    async def implement(self, stage: Stage, req: Requirement) -> StageOutcome:
        """为每个任务开隔离工位，**按依赖分层并发执行**。

        之前是 `for t in tasks: await ...` —— 串行。那样 Task/TaskTouch/contracts
        /decompose-critic 这一整套「拆成 N 个可同时跑的任务」的设计全部落空。

        现在按 depends_on 拓扑分层：同层内 asyncio.gather 并发，层间串行。
        并发度受 Project.quota_parallel_runs 限制（之前这个配额从没被读过）。
        """
        if self.caps.workspace is None:
            return StageOutcome(False, "workspace 未注入，无法开工位")

        tasks = self.s.execute(
            select(Task).where(Task.requirement_id == req.id).order_by(Task.key)
        ).scalars().all()
        if not tasks:
            return StageOutcome(False, "没有可执行的任务（拆解没产出）")

        project = self.s.get(Project, req.project_id)
        quota = max(1, int(getattr(project, "quota_parallel_runs", 4) or 4))
        specs = self._repo_specs(req.project_id)
        layers = topo_layers(tasks)

        done: list[str] = []
        failed: list[tuple[str, str]] = []
        sem = asyncio.Semaphore(quota)

        for layer in layers:
            # 前一层有失败就不再往下 —— 后面的任务依赖它
            if failed:
                for t in layer:
                    t.state = "blocked"
                continue
            results = await asyncio.gather(
                *(self._run_task(req, t, specs, sem) for t in layer),
                return_exceptions=True)
            for t, res in zip(layer, results):
                if isinstance(res, BaseException):
                    failed.append((t.key, f"{type(res).__name__}: {res}"))
                elif res is not True:
                    failed.append((t.key, str(res)))
                else:
                    done.append(t.key)
        self.s.flush()

        detail = f"完成 {len(done)}，失败 {len(failed)}"
        if failed:
            detail += "：" + "；".join(f"{k} {v[:80]}" for k, v in failed)
        return StageOutcome(not failed, detail,
                            {"done": done, "failed": [k for k, _ in failed],
                             "layers": len(layers), "concurrency": quota})

    async def _run_task(self, req: Requirement, task: Task, specs, sem) -> object:
        """跑一个任务。**无论成败都释放工位** —— 之前从不 release，100% 泄漏。"""
        async with sem:
            run = Run(project_id=req.project_id, task_id=task.id,
                      branch=f"cr/{req.seq}-{task.key.lower()}", state="running",
                      started_at=datetime.utcnow())
            self.s.add(run)
            self.s.flush()
            task.state = "running"

            mine = [x for x in specs if not task.repo_names or x.name in task.repo_names]
            ws = None
            port = None
            try:
                port = self._lease_port(req.project_id, run.id)
                ws = await self.caps.workspace.acquire(
                    project_id=req.project_id, run_id=run.id, branch=run.branch,
                    base_branch=self.target_branch(req.project_id), repos=mine, port=port)
                self._record_workspace(req.project_id, run.id, ws)

                reply = await self._code(ws, req, task)

                # **校验真的产生了 commit** —— 之前只要 agent 没抛异常就记 done，
                # agent 说"我不理解"、改错文件、一行没改都算成功
                shas = await self._collect_commits(
                    ws, run.branch, base=self.target_branch(req.project_id))
                if not shas:
                    # 把 agent 说了什么留下来，否则只看到一句
                    # "没有产生任何 commit"，完全不知道它干嘛去了
                    run.fail_log = (
                        "agent 回复：\n" + (getattr(reply, "text", "") or "(无输出)")[:4000]
                        + "\n\n工作区状态：\n" + await self._dirty(ws))
                    raise RuntimeError("agent 跑完了但没有产生任何 commit")
                # **只放仓名 → sha。**
                # 这里曾经顺手塞了一条 `"_workspace": <路径>`，而 merge 是
                # 拿 `commit_shas` 的键当仓名用的 —— 于是合并队列里会多出
                # 一个叫 `_workspace` 的幽灵仓，排队、报错、谁也找不到它。
                # 工位路径本来就在 Workspace 表里（_rehydrate 读的就是那张表），
                # 这份副本纯属多余。
                run.commit_shas = dict(shas)
                run.state = "done"
                run.finished_at = datetime.utcnow()
                task.state = "done"
                return True
            except Exception as exc:  # noqa: BLE001
                run.state = "failed"
                run.fail_reason = f"{type(exc).__name__}: {exc}"[:500]
                run.finished_at = datetime.utcnow()
                task.state = "failed"
                logger.exception("任务 %s 失败", task.key)
                return run.fail_reason
            finally:
                # **失败的工位不要立刻删。**
                # 之前失败即释放 —— 证据一起没了，只剩一句
                # "agent 跑完了但没有产生任何 commit"，根本没法查。
                # 留给 reaper 按 TTL 回收，中间人可以进去看。
                if ws is not None and task.state == "failed":
                    self._release_port(req.project_id, run.id)
                    logger.warning("任务 %s 失败，工位保留待查：%s", task.key, ws.root)
                self.s.flush()

    async def _code(self, ws, req: Requirement, task: Task):
        """让 agent 在工位里改代码。

        会话从拆解那次 fork —— 它已经读过需求背景和代码库，不用重新喂。
        """
        from vplatform.orchestration.dag import Stage as _S
        from vplatform.orchestration.handlers import skill_prompt

        touches = self.s.execute(
            select(TaskTouch.path).where(TaskTouch.task_id == task.id)
        ).scalars().all()
        # 不能靠会话继承（跨目录 fork 不成立），所以拆解结论要写进 prompt
        ctx = (
            f"需求：{req.title}\n{req.body}\n\n"
            f"本任务：{task.key} {task.title}\n"
            + (f"要交付什么：{task.delivers}\n" if task.delivers else "")
            + (f"预计触达：{', '.join(touches)}\n" if touches else "")
            + (("接口契约（必须遵守）：\n"
                + "\n".join(f"- {c}" for c in (req.contracts or [])) + "\n")
               if req.contracts else "")
            + "改完自行 `git add -A && git commit`。保持现有风格与命名。"
              "有构建/测试错误自己修到通过。"
        )
        prompt = skill_prompt(_S(key="implement", spec={"skill": "tdd"}), context=ctx)
        return await self._talk(req=req, purpose="code", prompt=prompt,
                                cwd=self.agent_cwd(ws), task=task)

    async def _dirty(self, ws) -> str:
        """工作区有没有未提交的改动 —— 判断 agent 是「没干活」还是「干了没提交」。"""
        out = []
        for name in (ws.repos or {}):
            r = await self.caps.workspace.exec(ws, ["git", "status", "--short"],
                                               cwd=name, timeout=60)
            out.append(f"[{name}] {(r.stdout or r.stderr).strip()[:600] or '(干净)'}")
        return "\n".join(out)

    async def resolve_base(self, ws, repo_name: str, target: str) -> str:
        """这个仓里，跟集成分支等价的、**真实存在**的 ref。

        **集成分支不一定存在。** 空间级的集成分支名（`vibe/dev`）在每个仓里
        是各自的一条分支，新接进来的仓只有自己的主干 —— `acquire` 已经会
        退到主干起步，但下游全都还假设集成分支存在。

        后果实测过一次，而且极其昂贵：agent 把功能写完、测试写完、
        377 个后端测试跑通、commit 也提了，平台却跑
        `rev-list vibe/dev..cr/12-t1` —— 这个 ref 不存在，命令 fatal，
        于是判定「没有产生任何 commit」，**把这份真实工作整个扔掉**，
        任务标失败。日志里只有一句「agent 跑完了但没有产生任何 commit」。
        """
        if self.caps.workspace is None:
            return target          # 没工位就问不了 git，原样返回
        for ref in (target, f"origin/{target}", "main", "origin/main",
                    "master", "origin/master"):
            r = await self.caps.workspace.exec(
                ws, ["git", "rev-parse", "--verify", "--quiet", ref],
                cwd=repo_name, timeout=30)
            if r.ok and (r.stdout or "").strip():
                return ref
        return target          # 都没有就原样返回，让下游报真实的错

    async def _collect_commits(self, ws, branch: str, *,
                               base: str) -> dict[str, str]:
        """每个仓在本分支上相对集成分支有没有新 commit。

        base 写死过 `vibe/dev`：换了集成分支名之后，这里对比的是一条
        不存在的 ref，`rev-list` 直接失败 → 判定「没产生 commit」→
        任务被判失败。**代码其实写好了。**
        """
        shas: dict[str, str] = {}
        for name, path in (ws.repos or {}).items():
            ref = await self.resolve_base(ws, name, base)
            cnt = await self.caps.workspace.exec(
                ws, ["git", "rev-list", "--count", f"{ref}..{branch}"], cwd=name)
            if cnt.ok and cnt.stdout.strip().isdigit() and int(cnt.stdout.strip()) > 0:
                head = await self.caps.workspace.exec(
                    ws, ["git", "rev-parse", "HEAD"], cwd=name)
                if head.ok:
                    shas[name] = head.stdout.strip()
        return shas

    async def _session_for(self, req: Requirement, *, purpose: str, cwd: str,
                           task: Task | None = None) -> tuple[str, bool, str | None]:
        """取会话。返回 (session_id, 要不要 fork, 已存在的真实 id)。

        **CLI 路径下会话 id 由 opencode 创建**，我们只能在首次 send 之后捕获
        （`--session` 的语义是「续接已存在的会话」，自己编一个会被
        `Session not found` 打回 —— 实测踩过）。所以这里可能返回一个
        待定标记，真实 id 由 `_remember_session()` 在 send 之后写回。
        """
        existing = self.s.execute(
            select(AgentSessionRow).where(
                AgentSessionRow.requirement_id == req.id,
                AgentSessionRow.task_id == (task.id if task else None),
                AgentSessionRow.purpose == purpose)
        ).scalar_one_or_none()
        # **只在同目录时才复用。**
        # 会话是绑工作目录的。serve 模式下每个工位一个 server，
        # 往一个「别的目录的 server 建的」会话发 prompt —— 服务端照样
        # 返回 204，然后**什么都不做**：事件流里一条都没有，
        # 任务跑到超时，日志里看不出任何原因。实测重试换了工位就必现。
        # 下面 fork 那段早就写了「只在同目录时才 fork」，复用这条却漏了。
        if existing is not None and existing.session_id and existing.cwd == cwd:
            return existing.session_id, False, existing.session_id   # 续改复用
        if existing is not None and existing.cwd != cwd:
            # 工位换了 —— 旧会话在旧目录里，这里必须重开一个
            self.s.delete(existing)
            self.s.flush()

        parent = None
        if task is not None:
            plan = self.s.execute(
                select(AgentSessionRow).where(
                    AgentSessionRow.requirement_id == req.id,
                    AgentSessionRow.purpose == "plan")
            ).scalar_one_or_none()
            # **只在同目录时才 fork。**
            # opencode 的会话绑定工作目录，fork 出来的子会话继承它。
            # 拆解会话在 plan 工位、实现任务在各自的 run 工位 —— 跨目录 fork
            # 会报 `Failed to init file picker: Invalid path`（那个工位已回收）。
            # 不同目录就老实新建会话，把拆解结论写进 prompt 而不是靠会话继承。
            if plan is not None and plan.session_id and plan.cwd == cwd:
                parent = plan.session_id

        sid = await self.caps.agent.create(cwd=cwd, title=self._title(req, task),
                                           parent=parent)
        return sid, parent is not None, None

    def _remember_session(self, req: Requirement, *, purpose: str, session_id: str,
                          cwd: str, parent: str | None = None,
                          task: Task | None = None) -> None:
        """把 opencode 给的真实会话 id 落库。

        不写回就等于没有会话：下一轮 refine 又会新建一个，
        「会话是一等公民」就只是句口号。
        """
        if not session_id:
            return
        row = self.s.execute(
            select(AgentSessionRow).where(
                AgentSessionRow.requirement_id == req.id,
                AgentSessionRow.task_id == (task.id if task else None),
                AgentSessionRow.purpose == purpose)
        ).scalar_one_or_none()
        if row is None:
            self.s.add(AgentSessionRow(
                project_id=req.project_id, requirement_id=req.id,
                task_id=task.id if task else None, session_id=session_id,
                parent_session_id=parent, purpose=purpose, cwd=cwd))
        else:
            row.session_id = session_id
            row.cwd = cwd
        self.s.flush()

    def _title(self, req: Requirement, task: Task | None) -> str:
        return f"{req.ref}{' ' + task.key if task else ''} {req.title}"

    @staticmethod
    def agent_cwd(ws) -> str:
        """agent 该在哪个目录里干活。

        单仓时**直接给仓库目录** —— 工位根不是 git 仓，opencode 的项目根
        检测会往上走、找不到就跑偏（实测跑到了平台自己的仓库里）。

        多仓时只能给工位根。实测过 opencode 1.17 拿到非 git 目录会怎样：
        它**不会**往上爬进外层仓库（`--dir` 钉住了，E14 不会重演），
        但会把会话归到内置的 `global` 项目，没有任何 VCS 上下文。
        所以多仓时必须靠 prompt 把各仓的绝对路径讲清楚 —— 见 repo_map()。
        """
        repos = ws.repos or {}
        if len(repos) == 1:
            return next(iter(repos.values()))
        return str(ws.root)

    @staticmethod
    def repo_map(ws) -> str:
        """告诉 agent 每个仓在哪。

        **多仓时这段不能省。** cwd 是工位根（不是 git 仓），opencode 给不出
        项目上下文，agent 只能靠这里的绝对路径找到代码。
        之前代码注释说「prompt 里说明各仓是子目录」，但 prompt 里根本没写。
        """
        repos = ws.repos or {}
        if len(repos) <= 1:
            return ""
        lines = "\n".join(f"- {name}：{path}" for name, path in sorted(repos.items()))
        return (f"\n这个工位里有 {len(repos)} 个 git 仓，各自的绝对路径：\n{lines}\n"
                f"工位根 {ws.root} 本身不是 git 仓 —— "
                f"所有 git 命令都要在上面某个仓的目录里跑。\n")

    async def _talk(self, *, req: Requirement, purpose: str, prompt: str, cwd: str,
                    task: Task | None = None):
        """跟 agent 说一句话，并把真实会话 id 落库。

        **边跑边把思考推到页面上。** 之前界面只能显示一句「正在看代码…」，
        一等好几分钟，人不知道它在干嘛、干到哪了、还是已经卡死了。
        """
        sid, fork, known = await self._session_for(req, purpose=purpose, cwd=cwd,
                                                   task=task)
        trace: list[dict] = []
        stream = f"req:{req.id}"
        bus = get_bus()
        bus.clear_live(stream)     # 上一次运行的残留别混进来

        async def on_event(ev) -> None:
            item = {"kind": ev.kind, "text": ev.text, **(ev.data or {})}
            # **落库的 trace 不收 `text`。**
            # reply.text 已经把所有文本片段拼成了结论，结论会作为消息正文
            # 存一遍 —— trace 再存一遍就是同一段话入库两次。
            # 实时推送仍然带上它：跑的过程中人想看到它在说什么。
            if ev.kind != "text":
                # **落库前按 part 合并。**
                # opencode 是逐 token 推的，一轮 1500+ 条增量；原样入库
                # 就是一个巨大的 JSON 数组，读写都慢，页面也没法用。
                # 同一个 part 的增量拼成一段（跟前端同一个口径）。
                pid = (ev.data or {}).get("part_id")
                if pid and trace and trace[-1].get("part_id") == pid:
                    trace[-1]["text"] += ev.text
                else:
                    trace.append(item)
            bus.publish_live(stream=stream, kind="agent_step",
                             payload={"purpose": purpose, **item})

        # **调 agent 之前先提交，把锁放掉。**
        # worker 是 `with session_scope() as s: await handler(ctx)` ——
        # 事务包住整个 handler。agent 一跑就是几分钟，这几分钟里
        # MySQL 一直握着前面写过的行锁（工位记录、事件、需求行）。
        # 于是用户在页面上发一句话，API 插 messages 时等锁等到
        # `Lock wait timeout exceeded`，前端就是一个红色的
        # Internal Server Error —— 实测用户就是这么撞上的。
        #
        # 提交之后再用同一个 session 会自动开新事务，后续的写照常回滚。
        # 这里提交掉的是「工位已建立」这类既成事实，本来就该落库。
        self.s.commit()
        reply = await self.caps.agent.send(sid, prompt, cwd=cwd, fork=fork,
                                           title=self._title(req, task),
                                           on_event=on_event)
        self._last_trace = trace
        if reply.session_id and reply.session_id != known:
            self._remember_session(req, purpose=purpose, session_id=reply.session_id,
                                   cwd=cwd, parent=sid if fork else None, task=task)
        return reply

    # ── 验证 ───────────────────────────────────────────────────
    async def verify(self, stage: Stage, req: Requirement) -> StageOutcome:
        """**真的执行** stage.commands，并与基线对比。

        之前这里返回「已跑 lint, test, build」但一条命令都没执行 ——
        UI 上会显示已跑、人会据此点批准，是整个仓库里最危险的一行代码。

        **必须与基线对比。** 只要 head 失败就判失败的话，任何测试环境不完整、
        或本来就有红灯的仓，每一条需求都会被卡住（实测：doBuyRight 在宿主上
        缺 flask/akshare，17 个 collection error —— 但那在 vibe/dev 上一模一样，
        跟这次改动毫无关系）。区分「这次改坏的」和「本来就坏的」才是可用的闸门。
        """
        if self.caps.workspace is None:
            return StageOutcome(True, "workspace 未注入，跳过验证", {"skipped": True})

        cmds = stage.commands
        if not cmds:
            return StageOutcome(True, "本环节未配置验证命令")

        runs = self._done_runs(req)
        if not runs:
            return StageOutcome(False, "没有成功的 Run，无可验证")

        target = self.target_branch(req.project_id)
        results: list[dict] = []
        regressions = 0
        for run in runs:
            ws = self._rehydrate(run)
            if ws is None:
                regressions += 1
                results.append({"run": run.branch, "cmd": "-", "ok": False,
                                "detail": "工位已回收，无法验证"})
                continue
            for repo_name in (ws.repos or {}):
                checks = await self.check_repo(ws, repo_name, cmds, run.branch,
                                               base=target,
                                               label=run.branch)
                for c in checks:
                    if not c["ok"]:
                        regressions += 1
                        run.state = "failed"
                        run.fail_reason = f"{c['cmd']} 回归"[:500]
                results.extend(checks)
        self.s.flush()

        passed = sum(1 for x in results if x["ok"])
        detail = f"{passed}/{len(results)} 项通过"
        if regressions:
            detail += f"（{regressions} 项是本次引入的回归）"
        return StageOutcome(regressions == 0, detail, {"checks": results})

    async def check_repo(self, ws, repo_name: str, cmds: list[str], branch: str,
                         *, base: str, label: str = "") -> list[dict]:
        """在一个仓上跑一组命令，**带基线对比**。

        `verify` 和合并后的 `_reverify` 共用这一份。
        之前两处各写各的，我修了 verify 却漏了 _reverify —— 同一个 bug
        在两个方法里活着，合并阶段照样被预存在的失败拦住。
        逻辑只留一份才不会再分叉。
        """
        out: list[dict] = []
        for cmd in cmds:
            argv = resolve_command(cmd, Path(ws.repos[repo_name]))
            if argv is None:
                out.append({"repo": repo_name, "cmd": cmd, "ok": True,
                            "baseline": "n/a", "run": label,
                            "detail": "该仓没有这个命令，跳过"})
                continue
            r = await self.caps.workspace.exec(ws, argv, cwd=repo_name, timeout=900)
            if r.ok:
                out.append({"repo": repo_name, "cmd": cmd, "ok": True,
                            "baseline": "n/a", "run": label})
                continue

            base_ok = await self._baseline_ok(ws, repo_name, argv, branch, base=base)
            if base_ok is None:
                verdict, note = False, "无法确认基线，保守判失败"
            elif base_ok:
                verdict, note = False, "基线通过、本次失败 → **本次改动引入的回归**"
            else:
                verdict, note = True, "基线同样失败 → 与本次改动无关，不算回归"
            out.append({"repo": repo_name, "cmd": cmd, "ok": verdict, "run": label,
                        "baseline": "pass" if base_ok else "fail",
                        "detail": f"{note}\n{(r.stderr or r.stdout)[-400:]}"})
        return out

    async def _baseline_ok(self, ws, repo_name: str, argv: list[str],
                           branch: str, *, base: str) -> bool | None:
        """同一条命令在基线分支上过不过。None 表示查不出来。

        用 `git stash` + `checkout` 而不是另开工位：另开工位要重新装依赖，
        一次验证变成几分钟。切回来失败的话宁可返回 None 也不能把工位留在
        错误的分支上 —— 后面的环节还要用它。
        """
        stash = await self.caps.workspace.exec(
            ws, ["git", "stash", "push", "-u", "-m", "vp-baseline"],
            cwd=repo_name, timeout=120)
        co = await self.caps.workspace.exec(
            ws, ["git", "checkout", "--detach",
                 await self.resolve_base(ws, repo_name, base)],
            cwd=repo_name, timeout=120)
        try:
            if not co.ok:
                return None
            r = await self.caps.workspace.exec(ws, argv, cwd=repo_name, timeout=900)
            return r.ok
        finally:
            back = await self.caps.workspace.exec(
                ws, ["git", "checkout", branch], cwd=repo_name, timeout=120)
            if "vp-baseline" in (stash.stdout or ""):
                await self.caps.workspace.exec(ws, ["git", "stash", "pop"],
                                               cwd=repo_name, timeout=120)
            if not back.ok:
                logger.error("切回分支 %s 失败，工位可能停在基线上：%s",
                             branch, back.stderr[:200])

    # ── AI 复核 ────────────────────────────────────────────────
    async def ai_review(self, stage: Stage, req: Requirement) -> StageOutcome:
        """缺陷轴（ocr）+ 自建过滤合并层。"""
        if self.caps.reviewer is None:
            return StageOutcome(True, "reviewer 未注入，跳过复核", {"skipped": True})

        runs = self._done_runs(req)
        if not runs:
            return StageOutcome(True, "没有成功的 Run，无可复核")

        from vplatform.review.adapter import ReviewNotInstalled, ReviewResult

        total, degraded = 0, 0
        for run in runs:
            ws = self._rehydrate(run)
            if ws is None:
                logger.warning("Run %s 的工位已回收，跳过复核", run.id)
                continue
            # **必须按仓逐个复核。**
            # ocr 要的是 git 仓路径，传工位根会直接报
            # "is not a git repository"（工位根只是装各仓的父目录）——
            # 跟 agent 那个 cwd 问题是同一类，实测都撞到过。
            for repo_name, repo_path in (ws.repos or {}).items():
                try:
                    res: ReviewResult = await self.caps.reviewer.review(
                        repo_path=repo_path,
                        base=await self.resolve_base(ws, repo_name,
                                                     self.target_branch(req.project_id)),
                        head=run.branch,
                        background=self._background(req),
                        rules_path=self._rules_path())
                except ReviewNotInstalled as exc:
                    # **工具没装 ≠ 复核不通过。** 让整条需求判失败是错的 ——
                    # 如实说跳过，人工审核照常进行。
                    logger.warning("AI 复核跳过：%s", exc)
                    return StageOutcome(True, str(exc), {"skipped": True})
                degraded += res.failed_requests
                findings = res.findings
                if self.caps.finding_filter is not None and findings:
                    findings = await self.caps.finding_filter.apply(findings)
                for f in findings:
                    self.s.add(FindingRow(
                        project_id=req.project_id, run_id=run.id, axis=f.axis,
                        severity=f.severity, category=f.category, path=f.path,
                        start_line=f.start_line, end_line=f.end_line, claim=f.claim,
                        failure_scenario=f.failure_scenario,
                        existing_code=f.existing_code,
                        suggestion_code=f.suggestion_code,
                        kept=f.kept, verdict_reason=f.verdict_reason,
                        confidence=f.confidence))
                    total += 1
        self.s.flush()

        # §9.7 ②：ocr 报 status=complete + 退出码 0 也可能有失败请求。
        # 必须把降级如实传上去，不能只看退出码。
        detail = f"{total} 条发现"
        if degraded:
            detail += f"（⚠ 降级运行：上游 {degraded} 个请求失败）"
        return StageOutcome(True, detail, {"findings": total, "degraded": degraded})

    def _rules_path(self) -> str | None:
        """复核规则文件。没有就让 ocr 用它的内置默认。"""
        from pathlib import Path as _P
        p = _P(__file__).resolve().parents[2] / "rules" / "default.json"
        return str(p) if p.is_file() else None

    def _background(self, req: Requirement) -> str:
        from vplatform.review.adapter import build_background
        return build_background(title=req.title, body=req.body,
                                contracts=list(req.contracts or []))

    # ── 预览 ───────────────────────────────────────────────────
    async def preview(self, stage: Stage, req: Requirement) -> StageOutcome:
        """把工位的预览地址回写。之前这个环节完全空转，
        「业务员看预览」这个 v1 卖点在 v2 里消失了。"""
        urls: dict[str, str] = {}
        for run in self._done_runs(req):
            lease = self.s.execute(
                select(PortLease).where(PortLease.workspace_id == run.id)
            ).scalar_one_or_none()
            if lease is None:
                continue
            host = os.environ.get("VP_PREVIEW_HOST", "127.0.0.1")
            urls[run.branch] = f"http://{host}:{lease.port}"
        if not urls:
            return StageOutcome(True, "没有可暴露的预览（端口未租到或工位已回收）",
                                {"previews": {}})
        # **租到端口 ≠ 有服务在跑。**
        # 这一环只分配端口，真正把应用起起来是部署适配器的事。
        # 不探测就报「预览就绪」的话：业务员点开是空白页，
        # 浏览器自检也会对着一个连不上的地址「通过」—— 实测就是这样。
        live = {b: u for b, u in urls.items() if await _port_open(u)}
        if not live:
            return StageOutcome(
                True, f"分到了 {len(urls)} 个预览地址，但**没有服务在监听** —— "
                      f"这一环只租端口，起应用要配 deploy 适配器",
                {"previews": {}, "allocated": urls, "serving": False})
        return StageOutcome(True, f"{len(live)} 个预览就绪",
                            {"previews": live, "serving": True})

    # ── 浏览器自检（ego-browser）──────────────────────────────
    async def browser_check(self, stage: Stage, req: Requirement) -> StageOutcome:
        """让 agent 用 ego-browser 打开预览环境，自己点一遍。

        `verify` 跑的是 lint/test/build —— 那些全过，页面照样可能是白屏、
        按钮点不动、接口 404。这一环补的是「像个人一样真去点」。

        ego lite 的 task space 天生就是隔离的：agent 在自己的空间里操作，
        不抢用户的标签页，又能复用用户已有的登录态 —— 跟本平台
        「每条需求一个隔离单元」是同一个思路，正好对上。
        """
        urls = self._preview_urls(req)
        if not urls:
            return StageOutcome(True, "没有可用的预览地址，跳过浏览器自检",
                                {"skipped": True})
        if not browser_available(self._settings()):
            # **如实降级。** 说「跳过」而不是「通过」——
            # 没检查过的东西不能标成检查过了。
            return StageOutcome(
                True, "宿主没装 ego lite（ego-browser 不在 PATH 上），跳过浏览器自检。"
                      "装了它这一环才会真去点页面。", {"skipped": True})
        if self.caps.agent is None or self.caps.workspace is None:
            return StageOutcome(True, "agent/workspace 未注入，跳过浏览器自检",
                                {"skipped": True})

        from vplatform.orchestration.handlers import skill_prompt

        specs = self._repo_specs(req.project_id)
        ws = await self.caps.workspace.acquire(
            project_id=req.project_id, run_id=f"browser-{req.id[:12]}",
            branch=f"browser/{req.seq}",
            base_branch=self.target_branch(req.project_id), repos=specs)
        try:
            listing = "\n".join(f"- {k}：{v}" for k, v in urls.items())
            bin_path = browser_bin(self._settings())
            prompt = skill_prompt(stage, context=(
                f"这条需求刚部好预览环境：\n{listing}\n\n"
                f"需求：{req.title}\n{req.body}\n\n"
                f"浏览器命令：`{bin_path}`（PATH 里可能没有，用这个绝对路径）\n"
                "用它打开预览，**像个真人一样把这条需求涉及的路径走一遍**："
                "该点的点、该填的填、该翻的翻。\n"
                "重点看：页面是不是白屏 / 控制台有没有报错 / "
                "接口是不是 4xx 5xx / 这条需求要的东西到底出现没有。\n"
                "最后按下面格式给结论，一行一条，没问题就只回 `PASS`：\n"
                "`[严重|一般] 在哪个页面做了什么 → 看到了什么`"))
            reply = await self._talk(req=req, purpose="browser", prompt=prompt,
                                     cwd=self.agent_cwd(ws))
        finally:
            await self._release(ws)

        text = (reply.text or "").strip()
        findings = [ln.strip() for ln in text.splitlines()
                    if ln.strip().startswith("[")]
        self._say(req, "agent", text or "（没有输出）", stage="browser_check",
                  trace=self.take_trace())

        # **连不上就不是「通过」。**
        # agent 说「端口没进程监听 / 连接被拒绝」时，它什么都没检查成；
        # 只看有没有 `[严重]` 条目的话，这种情况会被判通过 —— 实测撞到过，
        # 一条根本没被点过的需求带着「浏览器自检通过」进了人工审核。
        unreachable = any(k in text for k in
                          ("连接被拒绝", "无法连接", "并未启动", "没有进程监听",
                           "无进程监听", "ERR_CONNECTION", "ECONNREFUSED"))
        if unreachable:
            return StageOutcome(
                True, "预览环境没起来，浏览器自检**没能执行** —— 跳过，不是通过",
                {"skipped": True, "unreachable": True})

        blocking = [f for f in findings if f.startswith("[严重")]
        if blocking:
            # **严重问题要拦住。** 放它过去等于让人工审核去发现白屏。
            return StageOutcome(False, f"浏览器自检发现 {len(blocking)} 个严重问题",
                                {"findings": findings})
        return StageOutcome(True,
                            "浏览器自检通过" if not findings
                            else f"浏览器自检通过，{len(findings)} 条一般问题",
                            {"findings": findings})

    def _preview_urls(self, req: Requirement) -> dict[str, str]:
        """这条需求的预览地址 —— 跟 /previews 接口同一份口径。"""
        out: dict[str, str] = {}
        for run in self._done_runs(req):
            lease = self.s.execute(
                select(PortLease).where(PortLease.workspace_id == run.id)
            ).scalar_one_or_none()
            row = self.s.execute(
                select(WorkspaceRow).where(WorkspaceRow.run_id == run.id,
                                           WorkspaceRow.state == "ready")
            ).scalar_one_or_none()
            if lease is None or row is None:
                continue
            host = os.environ.get("VP_PREVIEW_HOST", "127.0.0.1")
            out[run.branch] = f"http://{host}:{lease.port}"
        return out

    # ── 合并（per-repo 串行 + 三档冲突 + 重跑验证）────────────────
    async def merge(self, stage: Stage, req: Requirement) -> StageOutcome:
        """审核通过后真的合并。

        之前 DISPATCH 里没有 "merge" 这个键 —— 环节直接 `{"ok": True}` 放行。
        于是 MergeQueue / ConflictLadder / touch_conflicts / wide_refactor_exempt
        全部零生产调用，前端「合并队列」页展示的是 seed 脚本造的假数据。
        """
        from vplatform.merge.conflict import ConflictLadder
        from vplatform.merge.queue import MergeQueue

        runs = self._done_runs(req)
        if not runs:
            return StageOutcome(False, "没有成功的 Run，无可合并")

        q = MergeQueue(self.s)
        repos = {name for r in runs for name in (r.commit_shas or {})}
        for repo_name in sorted(repos):
            q.enqueue(project_id=req.project_id, requirement_id=req.id,
                      repo_name=repo_name)

        # touches 相交的需求排到后面 —— 冲突预防前置到调度期（§8.3 保险 ①）。
        # wide refactor 的 touches 大面积相交是预期的，豁免（§8.4）。
        from vplatform.orchestration.handlers import touch_conflicts, wide_refactor_exempt
        if not wide_refactor_exempt(self.s, req.id):
            paths = set(self.s.execute(
                select(TaskTouch.path).join(Task, Task.id == TaskTouch.task_id)
                .where(Task.requirement_id == req.id)).scalars())
            risky = touch_conflicts(self.s, project_id=req.project_id, paths=paths,
                                    exclude_requirement=req.id)
            for repo_name in sorted(repos):
                q.reorder_by_touch_risk(project_id=req.project_id,
                                        repo_name=repo_name,
                                        risky_requirement_ids=risky)

        target = self.target_branch(req.project_id)
        ladder = ConflictLadder(mergiraf_bin=self._settings().mergiraf_bin,
                                ai_resolver=self._ai_conflict_resolver(req))
        merged, blocked = [], []
        for run in runs:
            ws = self._rehydrate(run)
            if ws is None:
                blocked.append((run.branch, "工位已回收"))
                continue
            for repo_name, repo_path in (ws.repos or {}).items():
                job = q.enqueue(project_id=req.project_id, requirement_id=req.id,
                                repo_name=repo_name)
                q.mark(job, "rebasing")
                # **合进的分支也要是真实存在的那条。**
                # 直接用集成分支名的话，仓里没有它就是
                # `fatal: invalid upstream 'vibe/dev'` —— 三档冲突梯子
                # 第一档就起不来，报出来却是「冲突未解决」，
                # 让人以为是代码冲突。实测走真需求时栽在这儿。
                onto = await self.resolve_base(ws, repo_name, target)
                res = await ladder.resolve(repo_path, onto=onto,
                                           branch=run.branch,
                                           session_id=self._session_id(req, run))
                q.mark(job, "conflict" if not res.resolved else "verifying",
                       ladder=res.as_json())
                if not res.resolved:
                    blocked.append((repo_name, "冲突未解决"))
                    continue

                # **rebase 后必须重跑验证** —— 并行分支各自过 ≠ 合起来过。
                # 这一步省了就是把集成回归推给生产。
                ok, why = await self._reverify(ws, repo_name, run.branch,
                                               base=onto)
                q_ladder = res.as_json() + [{"stage": "re-verify", "ok": ok,
                                             "detail": why}]
                if not ok:
                    q.mark(job, "conflict", ladder=q_ladder)
                    blocked.append((repo_name, "rebase 后出现回归"))
                    continue

                pushed, push_why = await self._push(ws, repo_name, req)
                # 没推成也算「本地合并完成」—— 冲突解了、验证过了，
                # 只是没送出去。状态用 merged_local 区分，不要糊成 merged。
                q.mark(job, "merged" if pushed else "merged_local",
                       ladder=q_ladder + [{"stage": "push", "ok": pushed,
                                           "detail": push_why}])
                merged.append(f"{repo_name}({'已推送' if pushed else '仅本地'})")
        self.s.flush()

        detail = f"合并 {len(merged)} 个仓"
        if blocked:
            detail += "；受阻：" + "、".join(f"{k}({v})" for k, v in blocked)
        return StageOutcome(not blocked, detail,
                            {"merged": merged, "blocked": [k for k, _ in blocked]})

    async def _reverify(self, ws, repo_name: str, branch: str, *,
                        base: str) -> tuple[bool, str]:
        """rebase 后重跑验证。**同样要基线对比。**

        §12 强调「并行分支各自过 ≠ 合起来过」，所以这一步不能省。
        但它拦的必须是「合起来才出现的问题」，不是仓库本来就有的红灯 ——
        否则任何测试环境不完整的仓，一条需求都合不进去。
        """
        checks = await self.check_repo(ws, repo_name, ["lint", "test", "build"],
                                       branch, base=base, label="re-verify")
        bad = [c for c in checks if not c["ok"]]
        if bad:
            logger.warning("rebase 后 %s 出现回归：%s", repo_name,
                           "、".join(c["cmd"] for c in bad))
            return False, "；".join(f"{c['cmd']}: {c.get('detail','')[:120]}" for c in bad)
        return True, "rebase 后 lint/test/build 无回归"

    async def _push(self, ws, repo_name: str, req: Requirement) -> tuple[bool, str]:  # noqa: D401
        """把 vibe/dev 推回远端。返回 (是否推了, 说明)。

        **不推的三种情况都要能区分**，不能都归成一句「没推」：
        平台配了干跑、没有 host 实现、推失败。
        """
        if not self._settings().push_enabled:
            return False, "平台配置 push_enabled=false（干跑模式），改动留在工位"
        if self.caps.host is None:
            return False, "未配置 GitHostAdapter，改动留在工位"
        repo = self.s.execute(
            select(ProjectRepo).where(ProjectRepo.project_id == req.project_id,
                                      ProjectRepo.name == repo_name)
        ).scalar_one_or_none()
        pat = None
        if repo is not None and repo.pat_ref:
            from vplatform.core.config import resolve_secret
            try:
                pat = resolve_secret(repo.pat_ref)
            except Exception:  # noqa: BLE001
                pat = None
        branch = self.target_branch(req.project_id)
        try:
            await self.caps.host.push(Path(ws.repos[repo_name]), branch, pat=pat)
            return True, f"已推送 {branch}"
        except Exception as exc:  # noqa: BLE001
            logger.exception("push %s 失败", repo_name)
            return False, f"push 失败：{type(exc).__name__}: {exc}"[:200]

    def _ai_conflict_resolver(self, req: Requirement):
        """第三档：AI 解冲突，**携带原会话**（它知道自己当初为什么这么改）。

        之前 ai_resolver 永远是 None —— 第三档只是个没人填的回调口子。
        """
        if self.caps.agent is None:
            return None

        async def resolve(repo_path, files, session_id):
            prompt = (
                "你正在解决一次 rebase 冲突。以下文件仍有冲突标记：\n"
                + "\n".join(f"- {f}" for f in files)
                + "\n\n把每个文件的 <<<<<<< / ======= / >>>>>>> 标记消掉，"
                  "**两边的意图都要保留**（不是二选一）。改完 git add 这些文件。"
                  "不要 commit，不要 rebase --continue —— 平台会做。"
            )
            await self.caps.agent.send(session_id or "", prompt, cwd=str(repo_path))
            from vplatform.merge.conflict import conflicted_files
            return await conflicted_files(repo_path)

        return resolve

    def _session_id(self, req: Requirement, run: Run) -> str | None:
        row = self.s.execute(
            select(AgentSessionRow).where(
                AgentSessionRow.requirement_id == req.id,
                AgentSessionRow.task_id == run.task_id)
        ).scalar_one_or_none()
        return row.session_id if row else None

    def _settings(self):
        from vplatform.core.config import get_settings
        return get_settings()

    # ── 集成测试 ───────────────────────────────────────────────
    async def integrate(self, stage: Stage, req: Requirement) -> StageOutcome:
        """在汇流分支上跑 e2e —— 回答「这些需求合在一起还对吗」。"""
        if self.caps.workspace is None:
            return StageOutcome(True, "workspace 未注入，跳过集成", {"skipped": True})
        runs = self._done_runs(req)
        checked = 0
        for run in runs:
            ws = self._rehydrate(run)
            if ws is None:
                continue
            for repo_name in (ws.repos or {}):
                for cmd in stage.commands or ["e2e"]:
                    argv = resolve_command(cmd, Path(ws.repos[repo_name]))
                    if argv is None:
                        continue
                    r = await self.caps.workspace.exec(ws, argv, cwd=repo_name,
                                                       timeout=1800)
                    checked += 1
                    if not r.ok:
                        return StageOutcome(False,
                                            f"{repo_name} 的 {cmd} 未通过",
                                            {"detail": (r.stderr or r.stdout)[-400:]})
        return StageOutcome(True, f"{checked} 项集成检查通过" if checked
                            else "没有可跑的集成测试（各仓未配置 e2e）")

    # ── 部署 ───────────────────────────────────────────────────
    async def deploy(self, stage: Stage, req: Requirement) -> StageOutcome:
        if self.caps.deployer is None:
            return StageOutcome(True, "deployer 未注入，跳过部署", {"skipped": True})
        run_id = await self.caps.deployer.trigger(
            project_id=req.project_id, env=stage.env or "test",
            ref=self.target_branch(req.project_id), meta={"requirement": req.id})
        return StageOutcome(True, f"已触发 {stage.env} 部署", {"deploy_run": run_id})

    # ── 工位持久化与回收 ───────────────────────────────────────
    def _record_workspace(self, project_id: str, run_id: str, ws) -> None:
        """把工位落库。

        之前 Workspace 表**全仓零写入** —— worker 崩溃后没有任何记录可查，
        worktree 和容器就永久泄漏了，reaper 想收也不知道收什么。
        """
        self.s.add(WorkspaceRow(
            project_id=project_id, run_id=run_id, path=str(ws.root),
            container_id=ws.container_id, image=ws.image, state="ready",
            repos=dict(ws.repos or {})))
        self.s.flush()

    async def _release(self, ws, *, best_effort: bool = True) -> None:
        """释放工位，**顺带把这个工位的 opencode server 关掉**。

        serve 必须在工位目录里起（见 ServerPool），所以一个工位一个进程。
        不关的话并行需求一多，几十个 server 常驻，端口和内存都会被吃光。
        """
        try:
            await self.caps.workspace.release(ws, best_effort=best_effort)
        finally:
            agent = self.caps.agent
            pool = getattr(agent, "pool", None)
            if pool is not None:
                try:
                    await pool.close(self.agent_cwd(ws))
                except Exception:  # noqa: BLE001
                    logger.warning("关 opencode server 失败，工位已释放", exc_info=True)

    def _mark_workspace_released(self, run_id: str) -> None:
        row = self.s.execute(
            select(WorkspaceRow).where(WorkspaceRow.run_id == run_id)
        ).scalar_one_or_none()
        if row is not None:
            row.state = "released"
            row.released_at = datetime.utcnow()
            self.s.flush()

    def _lease_port(self, project_id: str, run_id: str) -> int | None:
        """租一个预览端口。租不到不算失败 —— 没有预览仍能改代码。"""
        from vplatform.workspace.ports import NoPortAvailable, PortLeaseManager
        try:
            return PortLeaseManager(self.s).acquire(project_id=project_id,
                                                    workspace_id=run_id)
        except NoPortAvailable:
            logger.warning("空间 %s 端口段已满，本次工位不暴露预览", project_id)
            return None

    def _release_port(self, project_id: str, run_id: str) -> None:
        from vplatform.workspace.ports import PortLeaseManager
        PortLeaseManager(self.s).release(project_id=project_id, workspace_id=run_id)

    def _done_runs(self, req: Requirement) -> list:
        return list(self.s.execute(
            select(Run).join(Task, Task.id == Run.task_id)
            .where(Task.requirement_id == req.id, Run.state == "done")
        ).scalars())

    def _rehydrate(self, run: Run):
        """从 Workspace 表还原一个 handle，供后续环节（验证/复核）复用工位。"""
        from vplatform.workspace.provider import WorkspaceHandle
        row = self.s.execute(
            select(WorkspaceRow).where(WorkspaceRow.run_id == run.id,
                                       WorkspaceRow.state == "ready")
        ).scalar_one_or_none()
        if row is None or not Path(row.path).is_dir():
            return None
        return WorkspaceHandle(id=row.id, run_id=run.id, project_id=row.project_id,
                               root=Path(row.path), branch=run.branch,
                               repos=dict(row.repos or {}),
                               container_id=row.container_id, image=row.image)

    # ── 工具 ───────────────────────────────────────────────────
    def target_branch(self, project_id: str) -> str:
        """这个空间的集成分支。

        **不能写死 `vibe/dev`。** `Project.target_branch` 一直是可配的，
        但这里原来 8 处全是字面量 —— 配置改了没有任何效果，
        而且报不出错，只是默默在另一条分支上干活。
        """
        b = self.s.execute(
            select(Project.target_branch).where(Project.id == project_id)
        ).scalar_one_or_none()
        return b or "vibe/dev"

    def _repo_specs(self, project_id: str) -> list:
        """把 ProjectRepo 转成 RepoSpec，**并解析 PAT**。

        之前这里不解析 `pat_ref` —— 于是私有仓永远 clone 不下来，
        报的是「Authentication failed」，看不出是平台没传凭证。
        端到端接真实私有仓时才暴露。
        """
        from vplatform.core.config import SecretError, resolve_secret
        from vplatform.workspace.provider import RepoSpec

        rows = self.s.execute(
            select(ProjectRepo).where(ProjectRepo.project_id == project_id)
        ).scalars().all()
        specs = []
        for r in rows:
            pat = None
            if r.pat_ref:
                try:
                    pat = resolve_secret(r.pat_ref)
                except SecretError as exc:
                    # 私有仓没凭证就是跑不了 —— 明说，不要让它变成
                    # 一句看不懂的 "Authentication failed"
                    raise RuntimeError(
                        f"仓 {r.name} 配了 pat_ref={r.pat_ref!r} 但解析失败：{exc}"
                    ) from exc
            specs.append(RepoSpec(name=r.name, url=r.url,
                                  default_branch=r.default_branch, pat=pat))
        return specs


DISPATCH = {
    "clarify": "clarify",
    "decompose": "decompose",
    "implement": "implement",
    "verify": "verify",
    "ai_review": "ai_review",
    "preview": "preview",
    "browser_check": "browser_check",
    "merge": "merge",
    "integrate": "integrate",
    "deploy_test": "deploy",
    "release": "deploy",
}
