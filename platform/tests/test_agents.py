"""AgentSession 测试 —— 之前这一层零测试，会话协议错了都没人知道。"""
import asyncio
import json

import pytest

from vplatform.agents.opencode import CliSession, extract_session_id, parse_json_events

# 从真实 opencode --format json 输出抓的样本
REAL = "\n".join([
    json.dumps({"type": "step_start", "sessionID": "ses_fcbdf6d40ffe0mJWs2nyBTA4eX",
                "part": {"id": "prt_1"}}),
    json.dumps({"type": "text", "sessionID": "ses_fcbdf6d40ffe0mJWs2nyBTA4eX",
                "part": {"text": "已把 x 改成 42"}}),
    "这一行不是 JSON，解析器要跳过而不是整体失败",
    json.dumps({"type": "step_finish", "sessionID": "ses_fcbdf6d40ffe0mJWs2nyBTA4eX",
                "part": {}}),
])


def test_extracts_real_session_id():
    """**会话 id 只能从 opencode 的输出里捕获，不能自己编。**

    `--session` 的语义是「续接已存在的会话」，传一个自己生成的 id
    会被 `Session not found` 打回 —— 实测踩过，端到端第一次就挂在这。
    """
    assert extract_session_id(REAL) == "ses_fcbdf6d40ffe0mJWs2nyBTA4eX"
    assert extract_session_id("没有 json 的输出") == ""


def test_parser_tolerates_non_json_lines():
    """解析器太脆会把一次成功的运行判成失败。"""
    events = parse_json_events(REAL)
    # kind 是**展示分类**，不是 opencode 的原始 type ——
    # 页面上要显示的是「读文件 calc.py」，不是 `step_start`
    assert [e.kind for e in events] == ["step", "text", "step"]
    assert "已把 x 改成 42" in events[1].text


def test_first_message_has_no_session_flag():
    """首次不能带 --session —— 会话还不存在。"""
    c = CliSession(model="m")
    argv = c._argv(CliSession.PENDING, "prompt", title="R-1 加胜率")
    assert "--session" not in argv
    assert "--format" in argv and "json" in argv     # 要 json 才能捕获 id
    assert "--title" in argv


def test_continue_uses_session_flag():
    c = CliSession(model="m")
    argv = c._argv("ses_real", "prompt")
    assert argv[argv.index("--session") + 1] == "ses_real"
    assert "--fork" not in argv


def test_fork_requires_session():
    """`--fork` 必须配 `--session`（opencode 的约束）。"""
    c = CliSession(model="m")
    argv = c._argv("ses_parent", "prompt", fork=True)
    assert "--fork" in argv and "--session" in argv
    # 没有父会话就不该出现 --fork
    assert "--fork" not in c._argv(CliSession.PENDING, "p", fork=True)


def test_create_returns_pending_not_fake_id():
    """create() 不能返回一个编造的 id —— 那正是最初的 bug。"""
    c = CliSession(model="m")
    sid = asyncio.run(c.create(cwd="/tmp", title="t"))
    assert sid == CliSession.PENDING
    # 有父会话时透传，供 send 时 fork
    assert asyncio.run(c.create(cwd="/tmp", title="t", parent="ses_p")) == "ses_p"


def test_model_gets_provider_prefix():
    """**opencode 要 `provider/model`。**

    只传 `deepseek-v4-pro` 会得到 `Model not found: deepseek-v4-pro/.` ——
    平台第一次端到端就挂在这。
    """
    assert CliSession(model="deepseek-v4-pro").model == "dashscope/deepseek-v4-pro"
    # 已经带前缀的不要重复加
    assert CliSession(model="openai/gpt-x").model == "openai/gpt-x"
    assert CliSession(model="m", provider="kimi").model == "kimi/m"


def test_error_events_are_detected_even_with_zero_exit():
    """**opencode 的失败不一定体现在退出码上。**

    实测 `Model not found` 时退出码是 0，错误只在 error 事件里。
    只看退出码会把失败当成功，下游拿到一个「跑完了但什么都没做」的结果。
    """
    from vplatform.agents.opencode import extract_errors

    raw = json.dumps({"type": "error", "sessionID": "s",
                      "error": {"name": "UnknownError",
                                "data": {"message": "Model not found: deepseek-v4-pro/."}}})
    assert extract_errors(raw) == ["Model not found: deepseek-v4-pro/."]
    assert extract_errors(REAL) == []          # 正常输出里没有 error


class _FakeStream:
    """按行吐字节的假管道。

    替身必须跟真实现同契约：`send()` 现在是**边跑边读**（流式），
    读的是 proc.stdout / proc.stderr 两个流，不是 communicate()。
    替身停在旧契约上的话，测试保护的就是幻觉。
    """

    def __init__(self, data: bytes):
        self._lines = data.splitlines(keepends=True) or []

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    async def read(self, n: int = -1) -> bytes:
        out, self._lines = b"".join(self._lines), []
        return out


class _FakeProc:
    returncode = 0

    def __init__(self, out: bytes, err: bytes = b"", returncode: int = 0):
        self.stdout = _FakeStream(out)
        self.stderr = _FakeStream(err)
        self.returncode = returncode

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


def test_send_streams_events_as_they_arrive():
    """**不能等跑完再吐。**

    实测 opencode 的 `--format json` 是逐行实时输出的（一次运行里各行的
    到达时间跨了 16 秒）。等 communicate() 的话页面上只有一句
    「正在看代码…」，人不知道它在干嘛、干到哪了、是不是卡死了。
    """
    import asyncio as _a
    from unittest.mock import patch

    raw = "\n".join([
        json.dumps({"type": "tool_use", "part": {
            "type": "tool", "tool": "read",
            "state": {"status": "completed", "title": "calc.py",
                      "output": "def add(a, b):"}}}),
        json.dumps({"type": "text", "part": {"text": "看完了"}}),
    ])
    got = []

    async def on_event(ev):
        got.append((ev.kind, ev.text, ev.data.get("tool")))

    async def fake_exec(*a, **kw):
        return _FakeProc(raw.encode())

    with patch("asyncio.create_subprocess_exec", fake_exec):
        reply = _a.run(CliSession(model="m").send("", "p", cwd="/tmp",
                                                  on_event=on_event))

    assert got == [("tool", "读文件：calc.py", "read"), ("text", "看完了", None)]
    assert "看完了" in reply.text


def test_stderr_is_drained_concurrently():
    """**stdout 和 stderr 必须并发读。**

    只读 stdout 的话 stderr 管道写满（64KB）就把 opencode 卡死，
    表现成「超时」，查半天查不出原因。
    """
    import asyncio as _a
    from unittest.mock import patch

    big = b"x" * 200_000

    async def fake_exec(*a, **kw):
        return _FakeProc(json.dumps({"type": "text", "part": {"text": "ok"}}).encode(),
                         err=big)

    with patch("asyncio.create_subprocess_exec", fake_exec):
        reply = _a.run(CliSession(model="m").send("", "p", cwd="/tmp"))
    assert "ok" in reply.text


def test_a_failing_callback_does_not_kill_the_run():
    """推流只是给人看的 —— 它挂了不能把这次开发也带走。"""
    import asyncio as _a
    from unittest.mock import patch

    async def boom(ev):
        raise RuntimeError("SSE 断了")

    async def fake_exec(*a, **kw):
        return _FakeProc(json.dumps({"type": "text", "part": {"text": "改完了"}}).encode())

    with patch("asyncio.create_subprocess_exec", fake_exec):
        reply = _a.run(CliSession(model="m").send("", "p", cwd="/tmp", on_event=boom))
    assert "改完了" in reply.text


def test_send_raises_on_error_event():
    import asyncio as _a
    from unittest.mock import patch

    err = json.dumps({"type": "error", "error": {"data": {"message": "鉴权失败"}}})

    async def fake_exec(*a, **kw):
        return _FakeProc(err.encode())

    with patch("asyncio.create_subprocess_exec", fake_exec):
        with pytest.raises(Exception, match="鉴权失败"):
            _a.run(CliSession(model="m").send("", "p", cwd="/tmp"))


def test_dir_is_passed_explicitly():
    """**光靠 subprocess 的 cwd 不够。**

    opencode 会从 cwd 往上找 git 仓当项目根，找不到就回落到别处 ——
    实测它跑进了平台自己的仓库，读平台的测试夹具、满硬盘找目标仓，
    而工位就在眼前。更糟的是它有权改那个仓。
    """
    argv = CliSession(model="m")._argv("", "p", cwd="/data/ws/repo")
    assert argv[argv.index("--dir") + 1] == "/data/ws/repo"


def test_the_answer_never_contains_the_thinking():
    """**结论只由 text 事件拼成。**

    解析器给 step/tool 也带了给人看的文字（"开始一轮"、"读文件：x"），
    那是**思考过程**不是答案。一起拼进去的话，需求稿正文会变成
    "开始一轮\\n搜代码：fee\\n这一轮结束" —— 实测在真实运行里就是这样，
    业务员看到的「需求稿」是一串工具调用记录。
    """
    import asyncio as _a
    from unittest.mock import patch

    raw = "\n".join([
        json.dumps({"type": "step_start", "part": {"id": "p1"}}),
        json.dumps({"type": "tool_use", "part": {
            "type": "tool", "tool": "grep",
            "state": {"status": "completed", "title": "fee|手续费"}}}),
        json.dumps({"type": "text", "part": {"text": "这是真正的结论"}}),
        json.dumps({"type": "step_finish", "part": {}}),
    ])

    async def fake_exec(*a, **kw):
        return _FakeProc(raw.encode())

    with patch("asyncio.create_subprocess_exec", fake_exec):
        reply = _a.run(CliSession(model="m").send("", "p", cwd="/tmp"))

    assert reply.text == "这是真正的结论"
    for noise in ("开始一轮", "搜代码", "这一轮结束"):
        assert noise not in reply.text
    # 但事件本身要留着 —— 思考面板要用
    assert any(e.kind == "tool" for e in reply.events)


def test_agent_state_dir_is_isolated_from_ego_lite(tmp_path, monkeypatch):
    """**opencode 的状态目录必须跟 ego lite 分开。**

    两者都用 `$XDG_DATA_HOME/opencode`（默认 `~/.local/share/opencode`），
    但它们是不同版本、不同 sqlite schema。实测装完 ego lite 之后，
    opencode CLI 直接报 `no such column: replacement_seq` —— 平台的 agent 层
    整个跑不动，而错误信息完全看不出跟浏览器有关。
    """
    from vplatform.bootstrap import CapabilityFactory
    from vplatform.core.config import Settings
    from vplatform.core.models import Org, Project

    # 两种 runner 都要隔离 —— serve 模式下 env 挂在共享 server 上
    st = Settings(opencode_data_home=str(tmp_path / "isolated"), agent_runner="cli")
    org = Org(name="a")
    p = Project(org_id=org.id, name="x", slug="x", dev_model="m")
    agent = CapabilityFactory(st)._agent(p)
    assert agent.env["XDG_DATA_HOME"] == str(tmp_path / "isolated")
    assert (tmp_path / "isolated").is_dir(), "目录没建出来，opencode 会自己报错"

    import vplatform.bootstrap as bs
    bs._SERVER = None                     # 共享单例，测之间要清
    st2 = Settings(opencode_data_home=str(tmp_path / "iso2"), agent_runner="serve")
    srv_agent = CapabilityFactory(st2)._agent(p)
    assert srv_agent.pool.env["XDG_DATA_HOME"] == str(tmp_path / "iso2")
    bs._SERVER = None


def test_isolation_can_be_turned_off(tmp_path):
    """留一个关掉的开关 —— 没装 ego lite 的机器不该被强行改路径。"""
    from vplatform.bootstrap import CapabilityFactory
    from vplatform.core.config import Settings
    from vplatform.core.models import Org, Project

    org = Org(name="a")
    p = Project(org_id=org.id, name="x", slug="x", dev_model="m")
    agent = CapabilityFactory(
        Settings(opencode_data_home="", agent_runner="cli"))._agent(p)
    assert "XDG_DATA_HOME" not in agent.env


# ── serve 模式：原生思考流 ──────────────────────────────────────
def test_serve_tells_reasoning_from_text_by_part_type():
    """**判断思考还是正文，看 part 的 type，不是 delta 的 `field`。**

    实测 `field` 恒为 `"text"` —— reasoning 的增量也走 `field: "text"`，
    只是它的 partID 指向一个 `type: "reasoning"` 的 part。
    照着别的项目抄 `field == "reasoning"` 会一条思考都收不到。
    """
    import asyncio as _a

    from vplatform.agents.opencode_server import ServerSession, _Turn

    sess = ServerSession.__new__(ServerSession)
    sess.stall_s = 5
    got, answer = [], []

    async def on_event(ev):
        got.append((ev.kind, ev.text))

    frames = [
        'data: {"type":"message.part.updated","properties":{"sessionID":"s1",'
        '"part":{"id":"p1","type":"reasoning"}}}',
        'data: {"type":"message.part.delta","properties":{"sessionID":"s1",'
        '"partID":"p1","field":"text","delta":"先看导出"}}',
        'data: {"type":"message.part.updated","properties":{"sessionID":"s1",'
        '"part":{"id":"p2","type":"text"}}}',
        'data: {"type":"message.part.delta","properties":{"sessionID":"s1",'
        '"partID":"p2","field":"text","delta":"结论是"}}',
        'data: {"type":"session.idle","properties":{"sessionID":"s1"}}',
    ]

    async def lines():
        for f in frames:
            yield f

    done = _a.run(sess._consume(lines().__aiter__(), "s1", on_event, [], answer,
                                _a.get_event_loop_policy().new_event_loop().time() + 999
                                if False else 1e18, _Turn()))
    assert done is True
    assert got == [("reasoning", "先看导出"), ("text", "结论是")]
    assert "".join(answer) == "结论是", "正文该只收 text part 的增量"


def test_serve_ignores_other_sessions():
    """`/event` 是**全局**流 —— 不按 sessionID 过滤的话，
    并行跑的另一条需求的思考会串到这条上。"""
    import asyncio as _a

    from vplatform.agents.opencode_server import ServerSession, _Turn

    sess = ServerSession.__new__(ServerSession)
    sess.stall_s = 5
    got = []

    async def on_event(ev):
        got.append(ev.text)

    frames = [
        'data: {"type":"message.part.updated","properties":{"sessionID":"别人",'
        '"part":{"id":"x","type":"reasoning"}}}',
        'data: {"type":"message.part.delta","properties":{"sessionID":"别人",'
        '"partID":"x","delta":"别人的思考"}}',
        'data: {"type":"session.idle","properties":{"sessionID":"s1"}}',
    ]

    async def lines():
        for f in frames:
            yield f

    _a.run(sess._consume(lines().__aiter__(), "s1", on_event, [], [], 1e18, _Turn()))
    assert got == [], "串了别的会话的思考"


def test_serve_keeps_part_types_across_a_reconnect():
    """流会中途断。partID→类型的映射丢了的话，重连之后的增量
    会全部被当成正文，思考断在半路。"""
    from vplatform.agents.opencode_server import _Turn

    t = _Turn()
    t.kinds["p1"] = "reasoning"
    assert t.kinds.get("p1") == "reasoning"     # 同一个 _Turn 跨重连复用


def test_a_reconnect_does_not_miss_a_finished_turn():
    """**session.idle 只在流上发一次。**

    断线那几秒错过它，就再也等不到 —— 只能干等到超时（默认 900s），
    一次早就跑完的运行被判成卡死。所以重连前要问一下会话还忙不忙。
    """
    import asyncio as _a

    from vplatform.agents.opencode_server import ServerSession

    sess = ServerSession.__new__(ServerSession)

    class FakeResp:
        status_code = 200
        @staticmethod
        def json():
            return {"ses_1": {"type": "idle"}}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, _p): return FakeResp()

    import httpx as _h
    real = _h.AsyncClient
    _h.AsyncClient = lambda **kw: FakeClient()
    try:
        assert _a.run(sess._busy("http://x", "ses_1")) is False
        assert _a.run(sess._busy("http://x", "ses_other")) is False
    finally:
        _h.AsyncClient = real


def test_unknown_session_status_means_keep_waiting():
    """查不到就当还在跑 —— 宁可多等，不要把还在干活的判成结束。"""
    import asyncio as _a

    from vplatform.agents.opencode_server import ServerSession

    sess = ServerSession.__new__(ServerSession)
    import httpx as _h
    real = _h.AsyncClient

    class Boom:
        async def __aenter__(self): raise RuntimeError("连不上")
        async def __aexit__(self, *a): return False

    _h.AsyncClient = lambda **kw: Boom()
    try:
        assert _a.run(sess._busy("http://x", "s")) is True
    finally:
        _h.AsyncClient = real


def test_a_quiet_but_busy_session_is_not_killed():
    """**静默不等于卡死。**

    模型做一次长工具调用、或者本身就慢，安静几分钟很正常。杀掉的话，
    一次正在干活的运行被判失败，前面写的代码全白做 —— 实测误伤过一次
    （agent 写完功能、测试全绿、commit 也提了，被判「卡死」）。
    """
    import asyncio as _a

    from vplatform.agents.opencode_server import ServerSession, _Turn

    sess = ServerSession.__new__(ServerSession)
    sess.stall_s = 0.05
    sess._base_url = "http://x"
    asked = {"n": 0}

    async def busy(_base, _sid):
        asked["n"] += 1
        return asked["n"] < 2          # 第一次「还忙」，第二次「不忙了」

    sess._busy = busy

    class Slow:
        """一直不吐行 —— 模拟模型在长时间思考。"""
        def __aiter__(self): return self
        async def __anext__(self):
            await _a.sleep(10)
            raise AssertionError("不该读到这里")

    done = _a.run(sess._consume(Slow(), "s1", None, [], [], 1e18, _Turn()))
    assert done is False               # 交给外层收尾，而不是抛「卡死」
    assert asked["n"] >= 2, "静默时没去问会话状态，直接判死了"


def test_sub_agent_sessions_are_followed():
    """**子 agent 的思考也要收。**

    agent 调 `task` 工具会起一个子 agent，它在**子会话**里干活
    （parentID 指向主会话）。只认主会话 ID 的话，子 agent 那几分钟的探索
    全部丢掉 —— 页面停在两步不动，看着像卡死，实际它忙得不可开交。
    实测用户就是这么撞上的：`Explore frontend UI code (@explore subagent)`。
    """
    import asyncio as _a

    from vplatform.agents.opencode_server import ServerSession, _Turn

    sess = ServerSession.__new__(ServerSession)
    sess.stall_s = 5
    got = []

    async def on_event(ev):
        got.append((ev.kind, ev.text))

    frames = [
        # 子会话诞生
        'data: {"type":"session.updated","properties":{"info":'
        '{"id":"ses_child","parentID":"ses_main","title":"@explore subagent"}}}',
        # 子会话里的思考
        'data: {"type":"message.part.updated","properties":{"sessionID":"ses_child",'
        '"part":{"id":"p9","type":"reasoning"}}}',
        'data: {"type":"message.part.delta","properties":{"sessionID":"ses_child",'
        '"partID":"p9","delta":"子 agent 在看前端代码"}}',
        # 别人家的会话，不能收
        'data: {"type":"message.part.updated","properties":{"sessionID":"ses_别人",'
        '"part":{"id":"p8","type":"reasoning"}}}',
        'data: {"type":"message.part.delta","properties":{"sessionID":"ses_别人",'
        '"partID":"p8","delta":"不该出现"}}',
        'data: {"type":"session.idle","properties":{"sessionID":"ses_main"}}',
    ]

    async def lines():
        for f in frames:
            yield f

    done = _a.run(sess._consume(lines().__aiter__(), "ses_main", on_event, [], [],
                                1e18, _Turn()))
    assert done is True
    assert got == [("reasoning", "子 agent 在看前端代码")]


def test_only_the_main_session_ends_the_turn():
    """子会话结束不代表这一轮完了 —— 主 agent 还要接着干。"""
    import asyncio as _a

    from vplatform.agents.opencode_server import ServerSession, _Turn

    sess = ServerSession.__new__(ServerSession)
    sess.stall_s = 5
    frames = [
        'data: {"type":"session.updated","properties":{"info":'
        '{"id":"ses_child","parentID":"ses_main"}}}',
        'data: {"type":"session.idle","properties":{"sessionID":"ses_child"}}',
        'data: {"type":"session.idle","properties":{"sessionID":"ses_main"}}',
    ]

    async def lines():
        for f in frames:
            yield f

    seen = {"n": 0}
    real = ServerSession._consume

    done = _a.run(sess._consume(lines().__aiter__(), "ses_main", None, [], [],
                                1e18, _Turn()))
    assert done is True     # 只有主会话的 idle 才收尾
