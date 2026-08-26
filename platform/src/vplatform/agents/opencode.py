"""opencode 实现（§6）。

两条路径，同一个接口：

**CliSession（M0 止血，约 10 行的改动）**
    opencode run <prompt> --session <id> --dir <worktree> --model <m>
    `--session` / `--continue` / `--fork` / `--dir` opencode CLI 原生支持。
    这条路径当场作废 v1 那套「拼历史 + 让 agent 自己 git diff」。

**ServerSession（M3 正解）**
    每个 workspace 容器里跑一个 `opencode serve`，走 HTTP：
        POST /session              新建
        POST /session/:id/message  续发
        POST /session/:id/fork     分叉
        GET  /global/event         SSE 实时轨迹
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator

import httpx

from vplatform.agents.session import AgentError, AgentEvent, AgentReply

logger = logging.getLogger(__name__)

_SESSION_JSON = re.compile(r'"sessionID"\s*:\s*"(ses_[A-Za-z0-9]+)"')


class CliSession:
    """走 `opencode run` 的 CLI 路径。

    **会话 id 由 opencode 创建，我们只能捕获，不能自己编。**

    第一版这里自己发了个 `ses_<uuid>` 就往 `--session` 里传 —— opencode 直接
    报 `Session not found`。因为 `--session` 的语义是「**续接**已存在的会话」
    （`opencode run --help` 写的是 "session id to continue"），
    新会话只能由不带该参数的 run 隐式创建。实测踩过。

    正确协议：
        首次   `opencode run <prompt> --format json`        → 从事件流里解析真实 id
        续改   `opencode run <prompt> --session <真实 id>`   → 上下文接着上次
        分叉   `opencode run <prompt> --session <父> --fork` → 从父会话分出来

    所以 `create()` 只能返回一个「待定」标记，真实 id 在首次 `send()` 之后
    由 `AgentReply.session_id` 带回来 —— **调用方必须把它写回去**。
    """

    PENDING = ""          # 尚未建立会话的标记

    def __init__(self, *, binary: str = "opencode", model: str = "deepseek-v4-pro",
                 provider: str = "dashscope",
                 env: dict[str, str] | None = None, timeout: float = 900):
        self.bin = binary
        # **opencode 要 `provider/model` 格式。**
        # 只传 `deepseek-v4-pro` 会得到 `Model not found: deepseek-v4-pro/.`
        # —— 平台第一次端到端就挂在这，而且错误藏在 JSON 事件里、
        # 退出码还是 0，只看退出码根本发现不了。
        self.model = model if "/" in model else f"{provider}/{model}"
        self.env = env or {}
        self.timeout = timeout

    async def create(self, *, cwd: str, title: str, parent: str | None = None) -> str:
        """返回待定标记。真实会话在首次 send() 时由 opencode 创建。

        parent 非空表示要 fork —— 记下来，首次 send 时带 `--fork`。
        """
        return parent or self.PENDING

    def _argv(self, session_id: str, prompt: str, *, fork: bool = False,
              title: str = "", cwd: str = "") -> list[str]:
        argv = [self.bin, "run", prompt, "--model", self.model, "--format", "json"]
        if cwd:
            # **必须显式 --dir。**
            # 光靠 subprocess 的 cwd 不够：opencode 会从 cwd 往上找 git 仓
            # 当项目根，找不到就回落到别处。实测它跑到了平台自己的仓库里 ——
            # 读平台的测试夹具、满硬盘找目标仓，而工位就在眼前。
            # 更糟的是它有权改那个仓。
            argv += ["--dir", cwd]
        if session_id and session_id != self.PENDING:
            argv += ["--session", session_id]
            if fork:
                argv.append("--fork")
        elif title:
            argv += ["--title", title[:60]]
        return argv

    async def send(self, session_id: str, prompt: str, *, cwd: str,
                   timeout: float | None = None, fork: bool = False,
                   title: str = "", on_event=None) -> AgentReply:
        """跑一次。`on_event` 非空时**边跑边回调**每一条事件。

        opencode 的 `--format json` 是逐行实时吐的（实测：一次运行里各行的
        到达时间跨了 16 秒，不是结束时一次性 flush）。所以这里不能再用
        `communicate()` 等它跑完 —— 那样界面上只能显示「正在看代码…」，
        人不知道它在干嘛、干到哪了、是不是卡死了。
        """
        import os

        argv = self._argv(session_id, prompt, fork=fork, title=title, cwd=cwd)
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env={**os.environ, **self.env},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                self._pump(proc, on_event), timeout=timeout or self.timeout)
        except asyncio.TimeoutError as exc:
            proc.kill(); await proc.wait()
            raise AgentError(f"opencode 超时（{timeout or self.timeout}s）") from exc

        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise AgentError(
                f"opencode 非 0 退出 (rc={proc.returncode})"
                f"\n--- stderr ---\n{stderr[-1500:]}"
                f"\n--- stdout 尾部 ---\n{stdout[-1500:]}")

        # **必须查事件流里的 error。**
        # opencode 把模型不存在、鉴权失败这类错误放在 JSON 事件里，
        # 退出码可能仍是 0 —— 只看退出码会把失败当成功，
        # 下游拿到一个"跑完了但什么都没做"的结果。
        errs = extract_errors(stdout)
        if errs:
            raise AgentError("opencode 报错：" + "；".join(errs[:3]))

        real_id = extract_session_id(stdout) or (
            session_id if session_id != self.PENDING else "")
        events = parse_json_events(stdout)
        # **结论只由 text 事件拼成。**
        # 解析器现在给 step/tool 也带了给人看的文字（"开始一轮"、"读文件：x"），
        # 这些是**思考过程**，不是答案。一起拼进去的话，需求稿正文会变成
        # "开始一轮\n搜代码：fee\n这一轮结束" —— 实测就是这样。
        # 思考走 on_event 的实时通道和消息的 trace，两条路各走各的。
        text = "\n".join(e.text for e in events if e.kind == "text" and e.text)
        return AgentReply(session_id=real_id, text=text or stdout[-4000:], events=events)

    async def _pump(self, proc, on_event) -> tuple[bytes, bytes]:
        """读完两个管道，顺便把每一行转成事件回调出去。

        **stdout 和 stderr 必须并发读。** 只读 stdout 的话 stderr 管道写满
        （64KB）就把 opencode 卡死，表现成「超时」，查半天查不出原因。
        """
        async def read_out() -> bytes:
            chunks: list[bytes] = []
            while True:
                # readline 对超长行会抛 LimitOverrunError；用 readuntil 的
                # 宽松版本兜住，一行再长也不该让整次运行失败
                try:
                    raw = await proc.stdout.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    raw = await proc.stdout.read(65536)
                if not raw:
                    break
                chunks.append(raw)
                if on_event is None:
                    continue
                ev = parse_event_line(raw.decode("utf-8", "replace"))
                if ev is not None:
                    try:
                        await on_event(ev)
                    except Exception:  # noqa: BLE001
                        # 推流失败不能影响这次运行 —— 它只是给人看的
                        logger.exception("agent 事件回调失败，继续跑")
            return b"".join(chunks)

        out, err = await asyncio.gather(read_out(), proc.stderr.read())
        await proc.wait()
        return out, err

    async def fork(self, session_id: str, *, cwd: str) -> str:
        """CLI 的分叉发生在 send 时（`--session <父> --fork`），
        这里把父 id 传下去，由首次 send 完成实际分叉。"""
        return session_id

    async def stream(self, session_id: str) -> AsyncIterator[AgentEvent]:
        # CLI 模式没有独立事件流；轨迹在 send() 的返回里
        return
        yield  # pragma: no cover


def extract_session_id(stdout: str) -> str:
    """从 `--format json` 的事件流里取真实 session id。

    每个事件都带 sessionID，取第一个即可。
    """
    m = _SESSION_JSON.search(stdout)
    return m.group(1) if m else ""


def extract_errors(stdout: str) -> list[str]:
    """从事件流里挑出 error 事件。

    opencode 的失败**不一定体现在退出码上** —— 实测 `Model not found` 时
    退出码是 0，错误只在 `{"type":"error", ...}` 事件里。
    """
    out: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{") or '"error"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "error":
            continue
        e = d.get("error") or {}
        msg = (e.get("data") or {}).get("message") or e.get("name") or "未知错误"
        if msg not in out:
            out.append(str(msg))
    return out


# 工具名 → 给人看的动词。agent 的思考过程是给业务员看的，
# 不该直接把 `bash` / `glob` 这种词甩在页面上。
_TOOL_CN = {
    "bash": "执行命令", "read": "读文件", "write": "写文件", "edit": "改文件",
    "glob": "找文件", "grep": "搜代码", "list": "看目录", "webfetch": "抓网页",
    "todowrite": "记待办", "todoread": "看待办", "task": "起子任务",
}


def parse_event_line(line: str) -> AgentEvent | None:
    """一行 JSON → 一条可展示的事件。不是事件就返回 None。

    **流式和批量必须共用这一份。** 之前 verify / _reverify 各写各的，
    我修了一个漏了另一个，同一个 bug 在两处活着 —— 解析器分叉是同类陷阱。
    """
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return None

    raw_kind = str(d.get("type") or "log")
    part = d.get("part") or {}
    state = part.get("state") or {}

    if part.get("tool") or part.get("type") == "tool":
        tool = str(part.get("tool") or "tool")
        title = str(state.get("title") or "").strip()
        out = state.get("output")
        return AgentEvent(
            kind="tool",
            text=f"{_TOOL_CN.get(tool, tool)}：{title}" if title
                 else _TOOL_CN.get(tool, tool),
            data={"tool": tool, "title": title[:300],
                  "status": str(state.get("status") or ""),
                  # 只留个开头。工具输出可能是整个文件（实测一次 read
                  # 吐了 4000+ 字），全塞进事件会把 SSE 和面板都撑爆，
                  # 而人在思考面板里要看的是「它读了什么」不是文件内容。
                  "detail": str(out or "")[:800]})

    if raw_kind == "error":
        err = d.get("error") or {}
        msg = (err.get("data") or {}).get("message") or err.get("name") or str(err)
        return AgentEvent(kind="error", text=str(msg)[:2000], data={})

    text = part.get("text") or d.get("text") or ""
    if raw_kind in ("reasoning", "thinking") or part.get("type") == "reasoning":
        return AgentEvent(kind="reasoning", text=str(text)[:4000], data={})
    if text:
        return AgentEvent(kind="text", text=str(text)[:8000], data={})
    if raw_kind in ("step_start", "step_finish"):
        return AgentEvent(kind="step",
                          text="开始一轮" if raw_kind == "step_start" else "这一轮结束",
                          data={})
    return None


def parse_json_events(stdout: str) -> list[AgentEvent]:
    """把 opencode 的 JSON 事件流转成 AgentEvent（批量版）。

    容忍非 JSON 行（opencode 偶尔混输普通文本），跳过而不是整体失败 ——
    解析器太脆会把一次成功的运行判成失败。
    """
    out: list[AgentEvent] = []
    for line in stdout.splitlines():
        ev = parse_event_line(line)
        if ev is not None:
            out.append(ev)
    return out


class ServerSession:
    """走 `opencode serve` 的 HTTP API —— M3 正解。"""

    def __init__(self, base_url: str, *, model: str = "deepseek-v4-pro",
                 client: httpx.AsyncClient | None = None, timeout: float = 900):
        self.base = base_url.rstrip("/")
        self.model = model
        self._client = client
        self.timeout = timeout

    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _post(self, path: str, body: dict) -> dict:
        try:
            r = await self._c().post(f"{self.base}{path}", json=body)
            r.raise_for_status()
            return r.json() if r.content else {}
        except httpx.HTTPError as exc:
            raise AgentError(f"opencode server {path} 失败: {exc}") from exc

    async def create(self, *, cwd: str, title: str, parent: str | None = None) -> str:
        body: dict = {"title": title}
        if parent:
            body["parentID"] = parent
        data = await self._post("/session", body)
        sid = data.get("id") or data.get("sessionID")
        if not sid:
            raise AgentError(f"opencode 未返回 session id: {data}")
        return str(sid)

    async def send(self, session_id: str, prompt: str, *, cwd: str,
                   timeout: float | None = None, fork: bool = False,
                   title: str = "") -> AgentReply:
        if not session_id:
            session_id = await self.create(cwd=cwd, title=title or "session")
        elif fork:
            session_id = await self.fork(session_id, cwd=cwd)
        data = await self._post(f"/session/{session_id}/message", {
            "parts": [{"type": "text", "text": prompt}],
            "model": self.model,
        })
        text = data.get("text") or json.dumps(data, ensure_ascii=False)[:4000]
        return AgentReply(session_id=session_id, text=text)

    async def fork(self, session_id: str, *, cwd: str) -> str:
        """从某个点分叉出并行子任务 —— 上下文天然继承，不用重新喂。"""
        data = await self._post(f"/session/{session_id}/fork", {})
        sid = data.get("id") or data.get("sessionID")
        if not sid:
            raise AgentError(f"fork 未返回 session id: {data}")
        return str(sid)

    async def stream(self, session_id: str) -> AsyncIterator[AgentEvent]:
        """订阅 /global/event，过滤出本 session 的事件转成 AgentEvent。"""
        try:
            async with self._c().stream("GET", f"{self.base}/global/event") as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    props = payload.get("properties") or {}
                    if props.get("sessionID") not in (None, session_id):
                        continue
                    yield AgentEvent(kind=payload.get("type", "log"),
                                     text=str(props.get("text", ""))[:4000], data=props)
        except httpx.HTTPError as exc:
            raise AgentError(f"opencode 事件流中断: {exc}") from exc

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
