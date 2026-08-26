"""`opencode serve` 模式的 AgentSession（§6 的正解）。

为什么换掉 CLI：CLI 的 `--format json` 只吐 `step_start / tool_use / text /
step_finish` —— **拿不到 reasoning**，粒度也只到「一整段」。页面上能显示的
就只有「读文件：x」这种条目，看不到模型在想什么。

serve 模式给的是真流：

    message.part.updated   part 建立/变更（type: reasoning | text | tool | step-*）
    message.part.delta     **逐 token 增量**，按 partID 灌进上面那个 part
    session.idle           这一轮结束

**判断「思考」还是「正文」要看 part 的 type，不是 delta 的 `field`** ——
实测 `field` 恒为 `"text"`，reasoning 的增量也走 `field: "text"`，
只是它的 partID 指向一个 `type: "reasoning"` 的 part。照着别的项目抄
`field == "reasoning"` 会一条思考都收不到。

一个 server 服务所有工位：`directory` 是查询参数，不用每个工位起一个进程。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
from dataclasses import dataclass, field

import httpx

from vplatform.agents.session import AgentError, AgentEvent, AgentReply

logger = logging.getLogger(__name__)

# 一次 prompt 最多等多久没有任何事件就认为卡死了。
# 不设的话模型挂住会把 worker 占到天荒地老（实测撞到过 10 分钟没输出）。
_STALL_S = 240.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class OpencodeServer:
    """`opencode serve` 的进程管理。

    **一个进程服务所有工位** —— `directory` 是每次请求的查询参数。
    之前设想过每个工位一个 server，那会把端口和内存都吃光。
    """

    binary: str = "opencode"
    # serve 的工作目录。**必须给** —— 实测不给的话，会话虽然能用
    # `?directory=` 建到别处，但那一轮模型压根不会跑：prompt_async 返回 204，
    # 事件流里除了心跳什么都没有，看起来就像模型挂了。
    cwd: str = ""
    host: str = "127.0.0.1"
    port: int = 0
    env: dict = field(default_factory=dict)
    _proc: asyncio.subprocess.Process | None = None
    _drain: object = None
    # server 最后说过的几句话。它挂掉时日志里必须有死因 ——
    # 只有一句「已退出 (rc=1)」等于什么都没说。
    _tail: list = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def ensure(self) -> str:
        """保证 server 活着，返回 base url。已经活着就直接返回。"""
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None:
                return self.base
            if self._proc is not None:
                # 上一个死了 —— 端口也要重挑，旧端口可能还在 TIME_WAIT
                logger.warning("opencode serve 已退出 (rc=%s)，重新拉起。最后输出：\n%s",
                               self._proc.returncode, "\n".join(self._tail[-12:]))
                self.port = 0
                if self._drain is not None:
                    self._drain.cancel()
                    self._drain = None
            if shutil.which(self.binary) is None:
                raise AgentError(f"找不到 {self.binary} —— 装 opencode 或改 VP_OPENCODE_BIN")
            self.port = self.port or _free_port()
            if self.cwd:
                # 真要用了才建。**目录必须存在**，否则 serve 起不来。
                from pathlib import Path as _P
                _P(self.cwd).mkdir(parents=True, exist_ok=True)
            # **不能挂 PIPE 又不读。**
            # 管道缓冲 64KB 写满之后 server 会直接卡死 —— 表现成
            # 「/event 一条都不来」，跟服务没起来一模一样，极难定位。
            # 这个坑我在 CLI 那条路上刚修过一次，这里又踩了一遍。
            self._proc = await asyncio.create_subprocess_exec(
                self.binary, "serve", "--port", str(self.port), "--hostname", self.host,
                cwd=self.cwd or None, env={**os.environ, **self.env},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            self._drain = asyncio.gather(
                self._sink(self._proc.stdout), self._sink(self._proc.stderr, err=True))
            await self._wait_ready()
            logger.info("opencode serve 已就绪：%s", self.base)
            return self.base

    async def _sink(self, stream, *, err: bool = False) -> None:
        """把 server 的输出读掉。不读就会把它卡死（见 ensure 里的说明）。"""
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace").rstrip()[:300]
                self._tail.append(text)
                del self._tail[:-40]
                if err:
                    logger.debug("opencode serve: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            return

    async def _wait_ready(self, timeout: float = 40.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        async with httpx.AsyncClient(timeout=2) as c:
            while asyncio.get_running_loop().time() < deadline:
                if self._proc is not None and self._proc.returncode is not None:
                    # stderr 由 _sink 在读，这里不能再抢
                    raise AgentError(
                        f"opencode serve 启动即退出 (rc={self._proc.returncode})，"
                        f"日志见 DEBUG 级 vplatform.agents.opencode_server")
                try:
                    # **health 200 还不算就绪。**
                    # 实测 health 先返回，插件和模型目录要再过 ~1 秒才加载完；
                    # 在那之前发的 prompt 会被**静默丢掉** —— prompt_async
                    # 照样返回 204，事件流里除了心跳什么都没有，
                    # 看起来完全像模型挂了。所以要等到模型目录真的非空。
                    r = await c.get(f"{self.base}/api/model")
                    if r.status_code < 400:
                        data = r.json()
                        models = data.get("data", data) if isinstance(data, dict) else data
                        if models:
                            return
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.3)
        raise AgentError(f"opencode serve {timeout}s 内没就绪")

    async def close(self) -> None:
        if self._drain is not None:
            self._drain.cancel()
            try:
                await self._drain
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass                    # 取消是预期的，别往日志里刷噪音
            self._drain = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None


class ServerPool:
    """按目录复用 `opencode serve`。

    **serve 必须在这个目录里启动。** 实测：server 起在别处、只靠
    `?directory=` 指过来的话，会话能建、prompt_async 也返回 204，
    但那一轮**根本不会执行** —— 事件流里除了心跳什么都没有，
    看起来完全像模型挂了。所以工位换了就得换 server。

    同一个工位复用同一个 server（一条需求里澄清/拆解/写码都在那儿），
    工位回收时 `close(cwd)` 掉。
    """

    def __init__(self, binary: str = "opencode", env: dict | None = None):
        self.binary = binary
        self.env = dict(env or {})
        self._servers: dict[str, OpencodeServer] = {}
        self._lock = asyncio.Lock()

    async def get(self, cwd: str) -> OpencodeServer:
        async with self._lock:
            srv = self._servers.get(cwd)
            if srv is None:
                srv = OpencodeServer(binary=self.binary, env=dict(self.env), cwd=cwd)
                self._servers[cwd] = srv
            return srv

    async def close(self, cwd: str = "") -> None:
        async with self._lock:
            targets = ([self._servers.pop(cwd)] if cwd and cwd in self._servers
                       else list(self._servers.values()))
            if not cwd:
                self._servers.clear()
        for s in targets:
            await s.close()


class ServerSession:
    """走 `opencode serve` 的 AgentSession。"""

    PENDING = ""

    def __init__(self, pool: "ServerPool", *, model: str,
                 timeout: float = 900, stall_s: float = _STALL_S):
        self.pool = pool
        self.model = model
        self.timeout = timeout
        self.stall_s = stall_s

    def _model_body(self) -> dict:
        pid, _, mid = self.model.partition("/")
        return {"providerID": pid, "modelID": mid or pid}

    async def _base(self, cwd: str) -> str:
        return await (await self.pool.get(cwd)).ensure()

    async def _client(self, cwd: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=await self._base(cwd),
                                 timeout=httpx.Timeout(60.0))

    @staticmethod
    def _unwrap(payload: dict) -> dict:
        """响应统一包在 `{"data": ...}` 里，也见过不包的 —— 两种都认。"""
        return payload.get("data", payload) if isinstance(payload, dict) else {}

    async def create(self, *, cwd: str, title: str, parent: str | None = None) -> str:
        async with await self._client(cwd) as c:
            body: dict = {"title": title[:60] or "session"}
            if parent:
                body["parentID"] = parent
            r = await c.post("/session", params={"directory": cwd}, json=body)
            if r.status_code >= 400:
                raise AgentError(f"建会话失败 {r.status_code}: {r.text[:300]}")
            sid = self._unwrap(r.json()).get("id", "")
            if not sid:
                raise AgentError(f"opencode 没返回 session id: {r.text[:200]}")
            return str(sid)

    async def fork(self, session_id: str, *, cwd: str = "", **_: object) -> str:
        """原生 fork —— 保留父会话已经读过的上下文。"""
        async with await self._client(cwd) as c:
            r = await c.post(f"/session/{session_id}/fork",
                             params={"directory": cwd} if cwd else None, json={})
            if r.status_code >= 400:
                raise AgentError(f"fork 会话失败 {r.status_code}: {r.text[:300]}")
            return str(self._unwrap(r.json()).get("id") or session_id)

    async def send(self, session_id: str, prompt: str, *, cwd: str,
                   timeout: float | None = None, fork: bool = False,
                   title: str = "", on_event=None) -> AgentReply:
        if not session_id or session_id == self.PENDING:
            session_id = await self.create(cwd=cwd, title=title)
        elif fork:
            session_id = await self.fork(session_id, cwd=cwd)

        base = await self._base(cwd)
        self._base_url = base
        collected: list[AgentEvent] = []
        answer: list[str] = []

        limit = timeout or self.timeout
        deadline = asyncio.get_running_loop().time() + limit
        state = _Turn()
        sent = False
        attempts = 0

        # **流会中途断。** opencode 的 /event 是 chunked 的全局流，
        # 实测会在一轮里被服务端关掉（httpx 报 incomplete chunked read）。
        # 断了就当没收完，重连接着听 —— 直接失败的话，一次好好的运行
        # 会因为传输层抖动被判成挂了。
        while asyncio.get_running_loop().time() < deadline:
            async with httpx.AsyncClient(
                    base_url=base, timeout=httpx.Timeout(None, connect=10)) as c:
                try:
                    async with c.stream("GET", "/event") as stream:
                        if stream.status_code >= 400:
                            raise AgentError(f"订阅 /event 失败 {stream.status_code}")
                        lines = stream.aiter_lines()
                        logger.info("订阅 %s/event，会话 %s", base, session_id)
                        if not sent:
                            # **先挂上 /event 再发 prompt。** 反过来的话，
                            # 模型秒回的事件会在订阅建立前就发完，页面上一片空白。
                            await self._prompt(base, session_id, prompt, cwd)
                            sent = True
                        done = await self._consume(
                            lines, session_id, on_event, collected, answer,
                            deadline, state)
                        if done:
                            break
                except (httpx.RemoteProtocolError, httpx.ReadError,
                        httpx.ConnectError) as exc:
                    # **必须重新 ensure。**
                    # 断线可能是 server 进程死了 —— 只重连不重启的话，
                    # 就是对着一个不存在的端口每 0.5 秒试一次，
                    # 一路空转到超时，日志刷满「流中断」。实测撞到过。
                    attempts += 1
                    logger.info("/event 流中断（%s），第 %d 次重连",
                                type(exc).__name__, attempts)
                    await asyncio.sleep(min(2 ** min(attempts, 5), 30))
                    try:
                        base = await self._base(cwd)
                    except AgentError:
                        if attempts >= 5:
                            raise
                    if attempts >= 12:
                        raise AgentError("opencode serve 反复连不上，放弃这一轮")
                # **断线期间可能已经跑完了。**
                # session.idle 只在流上发一次，断的那几秒错过就再也等不到，
                # 只能干等到超时。所以每次重连前先问一下会话还忙不忙。
                if sent and not await self._busy(base, session_id):
                    break
        else:
            raise AgentError(f"opencode 超时（{limit}s）")

        return AgentReply(session_id=session_id, text="".join(answer),
                          events=collected)

    async def _busy(self, base: str, sid: str) -> bool:
        """这个会话还在跑吗。查不到就当还在跑（宁可多等，不要早退）。"""
        try:
            async with httpx.AsyncClient(base_url=base, timeout=10) as c:
                r = await c.get("/session/status")
                if r.status_code >= 400:
                    return True
                data = r.json()
                data = data.get("data", data) if isinstance(data, dict) else data
                st = (data or {}).get(sid) or {}
                return str(st.get("type") or "") == "busy"
        except Exception:  # noqa: BLE001
            return True

    async def _prompt(self, base: str, sid: str, prompt: str, cwd: str) -> None:
        async with httpx.AsyncClient(base_url=base, timeout=60) as c:
            r = await c.post(f"/session/{sid}/prompt_async",
                             params={"directory": cwd},
                             json={"parts": [{"type": "text", "text": prompt}],
                                   "model": self._model_body()})
            if r.status_code >= 400:
                raise AgentError(f"发 prompt 失败 {r.status_code}: {r.text[:300]}")

    _base_url = ""

    async def _consume(self, lines, sid: str, on_event, collected: list,
                       answer: list, deadline: float, state: "_Turn") -> bool:
        """消费全局事件流，只认这个会话的。收到 session.idle 返回 True。

        断线重连时 `state` 沿用同一份 —— partID→类型的映射丢了的话，
        重连之后的 delta 全部会被当成正文，思考就断在半路。
        """
        kinds, buf = state.kinds, state.buf
        state.sids.add(sid)

        while True:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                raise AgentError("opencode 超时")
            try:
                line = await asyncio.wait_for(lines.__anext__(),
                                              timeout=min(self.stall_s, left))
            except StopAsyncIteration:
                return False                    # 服务端关流 —— 让外层重连
            except asyncio.TimeoutError:
                # **静默不等于卡死。** 模型做一次长工具调用、或者本身就慢，
                # 安静几分钟很正常。杀掉的话，一次正在干活的运行被判失败，
                # 前面写的代码全白做 —— 实测误伤过一次。
                # 会话到底还忙不忙，问服务端最准。
                if await self._busy(self._base_url, sid):
                    continue
                return False        # 真的不忙了 —— 让外层去确认收尾

            if not line or not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue

            t = ev.get("type") or ""
            props = ev.get("properties") or {}

            # **子 agent 的思考也要收。**
            # agent 调 `task` 工具会起一个子 agent，它在**子会话**里干活
            # （parentID 指向主会话）。只认主会话 ID 的话，子 agent 那几分钟
            # 的探索全部丢掉 —— 页面停在两步不动，看着像卡死，
            # 实际它正忙得不可开交。实测用户就是这么撞上的。
            self._track_children(props, state.sids)

            ses = props.get("sessionID")
            if ses is not None and ses not in state.sids:
                continue                        # 全局流，别的需求的不要
            if t == "session.idle" and ses == sid:
                return True                     # 只认主会话结束
            if t == "session.error" or (t or "").startswith("error"):
                raise AgentError(f"opencode 报错：{json.dumps(props, ensure_ascii=False)[:300]}")

            if t in ("message.part.updated", "message.part"):
                await self._on_part(props.get("part") or {}, kinds, on_event, collected)
            elif t == "message.part.delta":
                pid = props.get("partID") or ""
                delta = props.get("delta") or ""
                if not pid or not delta:
                    continue
                kind = kinds.get(pid, "text")
                buf.setdefault(pid, []).append(delta)
                if kind == "text":
                    answer.append(delta)
                if on_event is not None:
                    await self._emit(on_event, collected, AgentEvent(
                        kind="reasoning" if kind == "reasoning" else "text",
                        text=delta, data={"part_id": pid, "delta": True}))

    @staticmethod
    def _track_children(props: dict, sids: set) -> None:
        """认领子会话。事件里的会话对象带 parentID，指向我们就收进来。"""
        for key in ("info", "session"):
            obj = props.get(key)
            if isinstance(obj, dict) and obj.get("parentID") in sids:
                cid = obj.get("id")
                if cid:
                    sids.add(cid)
        if props.get("parentID") in sids and props.get("id"):
            sids.add(props["id"])

    async def _on_part(self, part: dict, kinds: dict, on_event, collected: list) -> None:
        pid = part.get("id") or ""
        ptype = part.get("type") or ""
        if pid and ptype:
            kinds[pid] = ptype
        if ptype != "tool":
            return
        state = part.get("state") or {}
        status = str(state.get("status") or "")
        if status not in ("completed", "error"):
            return                              # pending/running 不刷屏
        tool = str(part.get("tool") or "tool")
        title = str(state.get("title") or "").strip()
        if on_event is not None:
            await self._emit(on_event, collected, AgentEvent(
                kind="tool",
                text=f"{_TOOL_CN.get(tool, tool)}：{title}" if title
                     else _TOOL_CN.get(tool, tool),
                data={"tool": tool, "title": title[:300], "status": status,
                      "detail": str(state.get("output") or "")[:800]}))

    @staticmethod
    async def _emit(on_event, collected: list, ev: AgentEvent) -> None:
        collected.append(ev)
        try:
            await on_event(ev)
        except Exception:  # noqa: BLE001 —— 推流失败不能带走这次运行
            logger.exception("agent 事件回调失败，继续跑")


@dataclass
class _Turn:
    """一轮里跨重连要保住的状态。"""

    kinds: dict = field(default_factory=dict)   # partID → reasoning|text|tool
    buf: dict = field(default_factory=dict)     # partID → 已收到的片段
    # 主会话 + 它起的子 agent 会话。跨重连要保住 —— 丢了的话重连之后
    # 子 agent 的思考又看不见了。
    sids: set = field(default_factory=set)


_TOOL_CN = {
    "bash": "执行命令", "read": "读文件", "write": "写文件", "edit": "改文件",
    "glob": "找文件", "grep": "搜代码", "list": "看目录", "webfetch": "抓网页",
    "todowrite": "记待办", "todoread": "看待办", "task": "起子任务",
}
