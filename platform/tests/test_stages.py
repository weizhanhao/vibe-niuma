"""环节执行体测试 —— 重点在「能力缺席时不能假装成功」。"""
import asyncio
from pathlib import Path

import pytest

from vplatform.core.models import (
    ProjectRepo, Requirement, Run, Task, Workspace, next_requirement_seq,
)
from vplatform.orchestration.dag import default_pipeline
from vplatform.orchestration.handlers import Capabilities
from vplatform.orchestration.stages import StageRunner


@pytest.fixture()
def req(session, project):
    session.add_all([
        ProjectRepo(project_id=project.id, name="orders-api", url="file:///tmp/a"),
        ProjectRepo(project_id=project.id, name="orders-web", url="file:///tmp/b"),
    ])
    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="订单列表按门店筛选", requested_by="chen",
                    contracts=["GET /orders?storeId= → Order[]"])
    session.add(r)
    session.flush()
    return r


def _run(coro):
    return asyncio.run(coro)


def test_decompose_without_agent_degrades_to_single_task(session, project, req):
    """agent 缺席时降级为单任务，**并标 degraded** ——
    不标的话下游会以为并行度 1 是 AI 的判断，其实是能力缺失。"""
    runner = StageRunner(Capabilities(), session)
    out = _run(runner.decompose(default_pipeline().get("decompose"), req))
    assert out.ok and out.data["degraded"] is True
    tasks = session.query(Task).filter_by(requirement_id=req.id).all()
    assert len(tasks) == 1
    assert set(tasks[0].repo_names) == {"orders-api", "orders-web"}


def test_implement_without_workspace_fails_loudly(session, project, req):
    """没有工位就是做不了 —— 必须返回 ok=False，不能悄悄过。"""
    out = _run(StageRunner(Capabilities(), session)
               .implement(default_pipeline().get("implement"), req))
    assert out.ok is False and "workspace" in out.detail


def test_implement_without_tasks_fails(session, project, req):
    class FakeWs: pass
    out = _run(StageRunner(Capabilities(workspace=FakeWs()), session)
               .implement(default_pipeline().get("implement"), req))
    assert out.ok is False and "拆解没产出" in out.detail


def test_review_without_reviewer_is_skipped_explicitly(session, project, req):
    out = _run(StageRunner(Capabilities(), session)
               .ai_review(default_pipeline().get("ai_review"), req))
    assert out.ok and out.data["skipped"] is True


def _done_run_with_workspace(session, project, req, tmp_path):
    """建一个「跑完了且工位还在」的 Run。

    **不再塞 commit_shas["_workspace"] 那种假数据** —— 生产从不写它，
    测试靠它绕过去，等于测了一条不存在的路径。现在走真的 Workspace 表 +
    真实存在的目录，跟 _rehydrate() 的实际读法一致。
    """
    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    ws_dir = tmp_path / "ws"
    (ws_dir / "orders-api").mkdir(parents=True, exist_ok=True)
    run = Run(project_id=project.id, task_id=t.id, branch="cr/1-t1", state="done",
              commit_shas={"orders-api": "abc123"})
    session.add(run); session.flush()
    session.add(Workspace(project_id=project.id, run_id=run.id, path=str(ws_dir),
                          state="ready", repos={"orders-api": str(ws_dir / "orders-api")}))
    session.flush()
    return run


def test_review_skips_when_workspace_already_reclaimed(session, project, req):
    """工位被回收后复核只能跳过 —— 不能拿一个不存在的路径去跑 ocr。"""
    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    session.add(Run(project_id=project.id, task_id=t.id, branch="b", state="done"))
    session.flush()

    class Boom:
        async def review(self, **kw):
            raise AssertionError("工位没了还去跑复核")

    out = _run(StageRunner(Capabilities(reviewer=Boom()), session)
               .ai_review(default_pipeline().get("ai_review"), req))
    assert out.ok and out.data["findings"] == 0


def test_review_surfaces_degraded_upstream(session, project, req, tmp_path):
    """**§9.7 ②**：ocr 报 status=complete + 退出码 0 也可能有失败请求。
    这个降级信号必须一路传到 UI，不能只看退出码。"""
    from vplatform.review.adapter import Finding, ReviewResult

    run = _done_run_with_workspace(session, project, req, tmp_path)

    class FakeReviewer:
        async def review(self, **kw):
            return ReviewResult(
                findings=[Finding(axis="defect", severity="high", category="bug",
                                  path="a.py", start_line=1, claim="金额单位错了")],
                failed_requests=3)          # ← 上游降级

    out = _run(StageRunner(Capabilities(reviewer=FakeReviewer()), session)
               .ai_review(default_pipeline().get("ai_review"), req))
    assert out.data["degraded"] == 3
    assert "降级运行" in out.detail
    assert out.data["findings"] == 1


def test_review_applies_filter_and_persists_verdict(session, project, req, tmp_path):
    from vplatform.core.models import Finding as FindingRow
    from vplatform.review.adapter import Finding, ReviewResult

    run = _done_run_with_workspace(session, project, req, tmp_path)

    class FakeReviewer:
        async def review(self, **kw):
            return ReviewResult(findings=[
                Finding(axis="defect", severity="high", category="bug", path="a.py",
                        start_line=1, claim="真 bug"),
                Finding(axis="defect", severity="low", category="style", path="b.py",
                        start_line=2, claim="风格建议"),
            ])

    class FakeFilter:
        async def apply(self, findings):
            for f in findings:
                f.kept = f.severity != "low"
                f.verdict_reason = "有失败场景" if f.kept else "纯风格，无失败场景"
                f.confidence = "high"
            return findings

    _run(StageRunner(Capabilities(reviewer=FakeReviewer(), finding_filter=FakeFilter()),
                     session).ai_review(default_pipeline().get("ai_review"), req))

    rows = session.query(FindingRow).all()
    assert len(rows) == 2                       # 两条都落库，便于回看
    assert {r.kept for r in rows} == {True, False}
    dropped = next(r for r in rows if not r.kept)
    assert "纯风格" in dropped.verdict_reason    # 裁决理由要留痕


def test_repo_specs_resolve_pat_for_private_repos(session, project, monkeypatch):
    """**私有仓必须能拿到凭证。**

    之前 _repo_specs 不解析 pat_ref，私有仓 clone 报
    "Authentication failed" —— 看不出是平台根本没传凭证。
    端到端接真实私有仓时才暴露。
    """
    monkeypatch.setenv("VP_TEST_PAT", "ghp_secret")
    session.add(ProjectRepo(project_id=project.id, name="private-repo",
                            url="https://github.com/me/private.git",
                            pat_ref="env:VP_TEST_PAT"))
    session.flush()

    specs = StageRunner(Capabilities(), session)._repo_specs(project.id)
    mine = next(x for x in specs if x.name == "private-repo")
    assert mine.pat == "ghp_secret"


def test_missing_pat_gives_readable_error(session, project, monkeypatch):
    """凭证解析不出要明说，不能让它退化成一句看不懂的认证失败。"""
    monkeypatch.delenv("VP_MISSING_PAT", raising=False)
    session.add(ProjectRepo(project_id=project.id, name="r",
                            url="https://x/r.git", pat_ref="env:VP_MISSING_PAT"))
    session.flush()
    with pytest.raises(RuntimeError, match="pat_ref.*解析失败"):
        StageRunner(Capabilities(), session)._repo_specs(project.id)


def test_stage_runner_has_every_method_it_calls():
    """**守卫：不能出现「调用还在、方法没了」。**

    批量重构时 `_code` 被误删过，而 `_run_task` 还在调它 ——
    单测都过，端到端跑到一半才炸 AttributeError。
    这里静态扫一遍 self.X(...) 的调用，确认方法都存在。
    """
    import ast
    import inspect

    from vplatform.orchestration import stages as mod

    src = inspect.getsource(mod)
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "StageRunner")
    defined = {n.name for n in cls.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    called = set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"):
            called.add(node.func.attr)

    missing = {c for c in called if c not in defined and not hasattr(StageRunner, c)}
    assert not missing, f"这些方法被调用但不存在：{sorted(missing)}"


def test_failed_task_keeps_workspace_for_inspection(session, project, req, tmp_path):
    """**失败的工位不能立刻删。**

    之前失败即释放 —— 证据一起没了，只剩一句「agent 跑完了但没有产生任何
    commit」，根本没法查它到底干嘛去了。留给 reaper 按 TTL 回收。
    """
    from vplatform.core.models import ProjectRepo as _PR

    session.add(_PR(project_id=project.id, name="r", url=str(tmp_path / "r")))
    session.flush()
    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x")
    session.add(t); session.flush()

    released = []

    class WS:
        root = tmp_path / "ws"
        repos = {"r": str(tmp_path / "ws" / "r")}
        container_id = None
        image = None
        id = "w1"

    class Prov:
        async def acquire(self, **kw): return WS()
        async def release(self, ws, best_effort=False): released.append(ws)
        async def exec(self, ws, argv, **kw):
            from vplatform.workspace.provider import ExecResult
            return ExecResult(0, "", "")

    class Agent:
        async def create(self, **kw): return "ses_x"
        async def send(self, *a, **kw):
            from vplatform.agents.session import AgentReply
            return AgentReply(session_id="ses_x", text="我不知道该改什么")
        async def fork(self, sid, **kw): return sid

    runner = StageRunner(Capabilities(workspace=Prov(), agent=Agent()), session)
    out = _run(runner.implement(default_pipeline().get("implement"), req))

    assert out.ok is False
    assert released == [], "失败的工位被删了，证据没了"
    run = session.query(Run).one()
    assert run.state == "failed"
    assert "我不知道该改什么" in (run.fail_log or ""), "agent 说了什么没留档"


def test_agent_cwd_is_the_repo_dir_when_single_repo(session, project):
    """单仓时给仓库目录 —— 工位根不是 git 仓，opencode 会跑偏。"""
    class WS:
        root = Path("/data/ws")
        repos = {"only": "/data/ws/only"}

    assert StageRunner(Capabilities(), session).agent_cwd(WS()) == "/data/ws/only"


def test_agent_cwd_falls_back_to_workspace_root_for_multi_repo(session, project):
    class WS:
        root = Path("/data/ws")
        repos = {"a": "/data/ws/a", "b": "/data/ws/b"}

    assert StageRunner(Capabilities(), session).agent_cwd(WS()) == "/data/ws"


def test_fork_only_happens_in_the_same_directory(session, project, req, tmp_path):
    """**opencode 的会话绑定工作目录。**

    拆解会话在 plan 工位、实现任务在各自的 run 工位。跨目录 fork 会报
    `Failed to init file picker: Invalid path`（那个工位已被回收）——
    实测端到端撞到过。不同目录就老实新建会话。
    """
    from vplatform.core.models import AgentSession as ASRow

    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x")
    session.add(t)
    session.add(ASRow(project_id=project.id, requirement_id=req.id,
                      session_id="ses_plan", purpose="plan",
                      cwd="/data/ws/plan/repo"))
    session.flush()

    runner = StageRunner(Capabilities(agent=_StubAgent()), session)

    # 同目录 → fork
    sid, fork, _ = _run(runner._session_for(req, purpose="code",
                                            cwd="/data/ws/plan/repo", task=t))
    assert fork is True and sid == "ses_plan"

    # 不同目录 → 不 fork，新建
    sid2, fork2, _ = _run(runner._session_for(req, purpose="code",
                                              cwd="/data/ws/run1/repo", task=t))
    assert fork2 is False


def test_session_cwd_is_persisted(session, project, req):
    from vplatform.core.models import AgentSession as ASRow

    runner = StageRunner(Capabilities(agent=_StubAgent()), session)
    runner._remember_session(req, purpose="plan", session_id="ses_a",
                             cwd="/data/ws/plan/repo")
    row = session.query(ASRow).one()
    assert row.cwd == "/data/ws/plan/repo"


class _StubAgent:
    """替身要跟真实现同契约：create(parent=X) 透传 parent，供 send 时 fork。
    替身behaviour 跟真实现不一致的话，测试保护的就是幻觉。"""

    async def create(self, *, cwd, title, parent=None):
        return parent or ""

    async def fork(self, sid, **kw):
        return sid

    async def send(self, *a, **kw):
        from vplatform.agents.session import AgentReply
        return AgentReply(session_id="ses_new")


def test_python_interpreter_is_resolved_at_runtime(tmp_path):
    """**不能写死 `python`。**

    macOS 和不少 Linux 发行版上只有 `python3`，写死会直接
    FileNotFoundError，报的还是「找不到 python」这种看不出上下文的错。
    实测端到端在 verify 环节撞到过。
    """
    from vplatform.orchestration.stages import python_bin, resolve_command

    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    argv = resolve_command("test", tmp_path)
    assert argv is not None
    assert argv[0] == python_bin()
    assert "\x00" not in argv[0]                 # 占位符必须被替换掉
    import shutil
    assert shutil.which(argv[0]), f"解析出的解释器 {argv[0]} 不存在"


def test_pytest_ini_alone_is_enough_to_run_tests(tmp_path):
    """有 pytest.ini 就该能跑测试 —— 不少项目不把 pytest 写进 requirements
    （doBuyRight 就是：104 个测试文件，requirements 里没有 pytest）。"""
    from vplatform.orchestration.stages import resolve_command

    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    argv = resolve_command("test", tmp_path)
    assert argv and "pytest" in argv


def _ws_stub(tmp_path, repo="r"):
    class WS:
        root = tmp_path
        repos = {repo: str(tmp_path / repo)}
        container_id = None
        image = None
        id = "w1"
    return WS()


def test_verify_does_not_blame_preexisting_failures(session, project, req, tmp_path):
    """**必须区分「这次改坏的」和「本来就坏的」。**

    只要 head 失败就判失败的话，任何测试环境不完整、或本来就有红灯的仓，
    每条需求都会被卡住 —— 实测 doBuyRight 在宿主上缺 flask，
    17 个 collection error，但那在基线上一模一样。
    """
    from vplatform.core.models import Workspace as WSRow

    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    run = Run(project_id=project.id, task_id=t.id, branch="cr/1-t1", state="done")
    session.add(run); session.flush()
    session.add(WSRow(project_id=project.id, run_id=run.id, path=str(tmp_path),
                      state="ready", repos={"r": str(tmp_path / "r")}))
    session.flush()

    class Prov:
        """test 命令在 head 和基线上都失败 —— 不算回归。"""
        async def exec(self, ws, argv, **kw):
            from vplatform.workspace.provider import ExecResult
            if argv[0] == "git":
                return ExecResult(0, "", "")
            return ExecResult(1, "", "ModuleNotFoundError: No module named 'flask'")

    out = _run(StageRunner(Capabilities(workspace=Prov()), session)
               .verify(default_pipeline().get("verify"), req))
    assert out.ok is True, f"基线同样失败不该判回归：{out.detail}"
    assert any("与本次改动无关" in c.get("detail", "") for c in out.data["checks"])


def test_verify_catches_real_regression(session, project, req, tmp_path):
    """基线通过、本次失败 → 就是这次改坏的，必须拦住。"""
    from vplatform.core.models import Workspace as WSRow

    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    run = Run(project_id=project.id, task_id=t.id, branch="cr/1-t1", state="done")
    session.add(run); session.flush()
    session.add(WSRow(project_id=project.id, run_id=run.id, path=str(tmp_path),
                      state="ready", repos={"r": str(tmp_path / "r")}))
    session.flush()

    class Prov:
        def __init__(self): self.on_base = False
        async def exec(self, ws, argv, **kw):
            from vplatform.workspace.provider import ExecResult
            if argv[:2] == ["git", "checkout"]:
                self.on_base = "vibe/dev" in argv
                return ExecResult(0, "", "")
            if argv[0] == "git":
                return ExecResult(0, "", "")
            return ExecResult(0, "", "") if self.on_base else ExecResult(1, "", "断言失败")

    out = _run(StageRunner(Capabilities(workspace=Prov()), session)
               .verify(default_pipeline().get("verify"), req))
    assert out.ok is False
    assert any("本次改动引入的回归" in c.get("detail", "") for c in out.data["checks"])
    assert session.query(Run).one().state == "failed"


def test_review_runs_per_repo_not_on_workspace_root(session, project, req, tmp_path):
    """**ocr 要的是 git 仓路径。**

    传工位根会直接报 "is not a git repository" —— 工位根只是装各仓的
    父目录。跟 agent 的 cwd 问题是同一类，实测都撞到过。
    """
    from vplatform.core.models import Workspace as WSRow
    from vplatform.review.adapter import ReviewResult

    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    run = Run(project_id=project.id, task_id=t.id, branch="cr/1-t1", state="done")
    session.add(run); session.flush()
    for name in ("api", "web"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    session.add(WSRow(project_id=project.id, run_id=run.id, path=str(tmp_path),
                      state="ready",
                      repos={"api": str(tmp_path / "api"), "web": str(tmp_path / "web")}))
    session.flush()

    seen = []

    class Reviewer:
        async def review(self, *, repo_path, **kw):
            seen.append(repo_path)
            return ReviewResult()

    _run(StageRunner(Capabilities(reviewer=Reviewer()), session)
         .ai_review(default_pipeline().get("ai_review"), req))

    assert sorted(seen) == sorted([str(tmp_path / "api"), str(tmp_path / "web")])
    assert str(tmp_path) not in seen, "把工位根传给 ocr 了"


def test_dry_run_skips_push_and_says_so(session, project, req, monkeypatch, tmp_path):
    """**不推远端时必须留痕。**

    三种「没推」要能区分：干跑配置、没有 host 实现、推失败。
    都归成一句「没推」的话，运维看不出到底发生了什么。
    """
    from vplatform.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VP_PUSH_ENABLED", "false")

    class Host:
        async def push(self, *a, **kw):
            raise AssertionError("干跑模式不该真推")

    class WS:
        root = tmp_path
        repos = {"r": str(tmp_path / "r")}

    runner = StageRunner(Capabilities(host=Host()), session)
    pushed, why = _run(runner._push(WS(), "r", req))
    assert pushed is False
    assert "干跑" in why and "push_enabled" in why
    get_settings.cache_clear()


def test_missing_host_is_distinguishable_from_dry_run(session, project, req, tmp_path):
    class WS:
        root = tmp_path
        repos = {"r": str(tmp_path / "r")}

    pushed, why = _run(StageRunner(Capabilities(), session)._push(WS(), "r", req))
    assert pushed is False
    assert "GitHostAdapter" in why and "干跑" not in why


def test_merge_reverify_also_ignores_preexisting_failures(session, project, req, tmp_path):
    """**合并后的重跑验证要和 verify 用同一套基线逻辑。**

    之前两处各写各的：我修了 verify 却漏了 _reverify，
    于是合并阶段照样被预存在的失败拦住，需求永远合不进去。
    逻辑抽成一份才不会再分叉。
    """
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    class WS:
        root = tmp_path
        repos = {"r": str(tmp_path / "r")}

    class Prov:
        async def exec(self, ws, argv, **kw):
            from vplatform.workspace.provider import ExecResult
            if argv[0] == "git":
                return ExecResult(0, "", "")
            return ExecResult(1, "", "ModuleNotFoundError: No module named 'flask'")

    ok, why = _run(StageRunner(Capabilities(workspace=Prov()), session)
                   ._reverify(WS(), "r", "cr/1-t1", base="vibe/dev"))
    assert ok is True, f"基线同样失败不该拦住合并：{why}"


# ── 澄清 ────────────────────────────────────────────────────────
class _AskingAgent:
    """会提问的 agent 替身。"""

    def __init__(self, text):
        self.text = text
        self.prompts: list[str] = []

    async def create(self, *, cwd, title, parent=None):
        return parent or "ses_clarify"

    async def fork(self, sid, **kw):
        return sid

    async def send(self, sid, prompt, **kw):
        from vplatform.agents.session import AgentReply
        self.prompts.append(prompt)
        return AgentReply(session_id=sid or "ses_clarify", text=self.text)


class _NullWs:
    """acquire/release 都不做事的工位替身 —— 澄清只要有个 cwd。"""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.repos = {}

    async def acquire(self, **kw):
        return self

    async def release(self, ws, best_effort=False):
        return None


def _clarify(session, req, agent, tmp_path):
    runner = StageRunner(Capabilities(agent=agent, workspace=_NullWs(tmp_path)), session)
    return _run(runner.clarify(default_pipeline().get("clarify"), req))


def _msgs(session, req):
    from vplatform.core.models import Message
    return session.query(Message).filter_by(requirement_id=req.id) \
        .order_by(Message.created_at).all()


def test_clarify_asks_and_parks(session, project, req, tmp_path):
    """信息不足时提问并挂起。**不挂起就等于没澄清** —— 之前这个环节
    在 DISPATCH 里根本没条目，直接空转过去了。"""
    agent = _AskingAgent("1. 筛选是单选还是多选？\n2. 要不要记住上次的选择？")
    out = _clarify(session, req, agent, tmp_path)
    assert out.ok and out.data["awaiting"] is True and out.data["questions"] == 2
    m = _msgs(session, req)[-1]
    assert m.role == "agent" and m.awaiting_answer is True
    assert "单选还是多选" in m.body


def test_clarify_lets_a_ready_answer_through(session, project, req, tmp_path):
    out = _clarify(session, req, _AskingAgent("READY"), tmp_path)
    assert out.ok and not out.data.get("awaiting")
    assert _msgs(session, req)[-1].awaiting_answer is False


def test_clarify_waits_while_a_question_is_unanswered(session, project, req, tmp_path):
    """已经问了还没答就别再问一遍 —— 重复提问会把对话刷成一堵墙。"""
    from vplatform.core.models import Message
    session.add(Message(project_id=project.id, requirement_id=req.id, role="agent",
                        author="ai", body="单选还是多选？", stage="clarify",
                        awaiting_answer=True))
    session.flush()
    agent = _AskingAgent("又一个问题？")
    out = _clarify(session, req, agent, tmp_path)
    assert out.data["awaiting"] == 1
    assert agent.prompts == [], "还有问题没答就不该再调 agent"


def test_user_can_skip_the_questions(session, project, req, tmp_path):
    """「✓ 够了直接干」必须真的跳过 —— 不然业务员被无限追问困住。"""
    from vplatform.core.models import Message
    session.add(Message(project_id=project.id, requirement_id=req.id, role="user",
                        author="chen", body="✓ 够了直接干\n按你的判断来",
                        stage="clarify"))
    session.flush()
    agent = _AskingAgent("还有一个问题？")
    out = _clarify(session, req, agent, tmp_path)
    assert out.ok and out.data["skipped_by_user"] is True
    assert agent.prompts == []


def test_clarify_stops_after_three_rounds(session, project, req, tmp_path):
    """问三轮还问不清就开工，让实现阶段去暴露问题。"""
    from vplatform.core.models import Message
    for i in range(3):
        session.add(Message(project_id=project.id, requirement_id=req.id, role="agent",
                            author="ai", body=f"问题{i}？", stage="clarify",
                            awaiting_answer=False))
        session.add(Message(project_id=project.id, requirement_id=req.id, role="user",
                            author="chen", body=f"答{i}", stage="clarify"))
    session.flush()
    agent = _AskingAgent("再问一个？")
    out = _clarify(session, req, agent, tmp_path)
    assert out.ok and out.data["rounds"] == 3
    assert agent.prompts == []


def test_clarify_prompt_carries_the_whole_transcript(session, project, req, tmp_path):
    """第二轮必须带上之前问过什么、答过什么，否则 agent 会重复问。"""
    from vplatform.core.models import Message
    session.add(Message(project_id=project.id, requirement_id=req.id, role="agent",
                        author="ai", body="按门店还是按区域？", stage="clarify"))
    session.add(Message(project_id=project.id, requirement_id=req.id, role="user",
                        author="chen", body="按门店", stage="clarify"))
    session.flush()
    agent = _AskingAgent("READY")
    _clarify(session, req, agent, tmp_path)
    assert "按门店还是按区域？" in agent.prompts[0]
    assert "按门店" in agent.prompts[0]


def test_clarify_without_agent_does_not_pretend_to_have_asked(session, project, req):
    out = _run(StageRunner(Capabilities(), session)
               .clarify(default_pipeline().get("clarify"), req))
    assert out.ok and out.data["skipped"] is True


@pytest.mark.parametrize("text,want", [
    ("1. 单选还是多选?\n2. 要记住吗？", 2),
    ("READY", 0),
    ("- 这个字段是必填的吗？\n- 这个字段是必填的吗？", 1),   # 去重
    ("a?\nb?", 0),                                          # 太短，不是问题
    ("1?\n2?\n3?\n很长的一个问题要不要保留呢？", 1),
])
def test_question_extraction(text, want):
    from vplatform.orchestration.stages import _extract_questions
    assert len(_extract_questions(text)) == want


# ── 集成分支要读配置，不能写死 ──────────────────────────────────
def test_target_branch_comes_from_the_space_config(session, project, req):
    """`Project.target_branch` 一直是可配的，但 stages 里 8 处全是字面量
    `"vibe/dev"` —— 配置改了没有任何效果，还不报错，只是默默在另一条分支上干活。"""
    runner = StageRunner(Capabilities(), session)
    assert runner.target_branch(project.id) == "vibe/dev"
    project.target_branch = "integration"
    session.flush()
    assert runner.target_branch(project.id) == "integration"


def test_clarify_acquires_on_the_configured_branch(session, project, req, tmp_path):
    """澄清/拆解拿的是全空间的仓，起步分支必须跟着空间配置走。"""
    project.target_branch = "integration"
    session.flush()
    seen = {}

    class WS:
        root = tmp_path
        repos = {}
        async def acquire(self, **kw):
            seen.update(kw)
            return self
        async def release(self, ws, best_effort=False): return None

    _run(StageRunner(Capabilities(agent=_AskingAgent("READY"), workspace=WS()),
                     session).clarify(default_pipeline().get("clarify"), req))
    assert seen["base_branch"] == "integration"


def test_commit_detection_uses_the_configured_branch(session, project, req):
    """base 写死 `vibe/dev` 时，换了集成分支名就是在跟一条不存在的 ref 比 ——
    `rev-list` 失败 → 判定「没产生 commit」→ 任务被判失败，代码其实写好了。"""
    calls = []

    class WS:
        project_id = project.id
        repos = {"api": "/tmp/api"}

    class Prov:
        async def exec(self, ws, argv, **kw):
            calls.append(argv)
            from vplatform.workspace.provider import ExecResult
            return ExecResult(0, "0", "")

    _run(StageRunner(Capabilities(workspace=Prov()), session)
         ._collect_commits(WS(), "cr/1-t1", base="integration"))
    assert any("integration..cr/1-t1" in a for argv in calls for a in argv)


# ── 立需求那段对话 ──────────────────────────────────────────────
def _draft_req(session, project):
    from vplatform.core.models import Requirement, next_requirement_seq
    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="导出太难用了", body="导出太难用了", requested_by="chen",
                    stage="intake", state="draft")
    session.add(r); session.flush()
    return r


DRAFT_REPLY = (
    "看了下代码。\n```需求稿\n标题: 订单导出支持自定义列\n"
    "背景: 现在导出列写死在 exporter.py\n要做什么:\n- 弹窗里可勾选列\n"
    "验收标准:\n- [ ] 勾选后导出只含所选列\n```")


def _refine(session, r, agent, tmp_path):
    runner = StageRunner(Capabilities(agent=agent, workspace=_NullWs(tmp_path)),
                         session)
    return _run(runner.refine_draft(r))


def test_intake_asks_before_it_drafts(session, project, tmp_path):
    r = _draft_req(session, project)
    agent = _AskingAgent("1. 是每个人一套配置还是全公司一套？\n2. 要记住上次的选择吗？")
    out = _refine(session, r, agent, tmp_path)
    assert out["awaiting"] is True and out["questions"] == 2
    assert _msgs(session, r)[-1].awaiting_answer is True


def test_intake_writes_the_draft_back_onto_the_requirement(session, project, tmp_path):
    """谈成型后需求稿要落到 title/body 上 —— 人接着就在这上面改、确认。"""
    r = _draft_req(session, project)
    out = _refine(session, r, _AskingAgent(DRAFT_REPLY), tmp_path)
    assert out["ready"] is True
    assert r.title == "订单导出支持自定义列"
    assert "勾选后导出只含所选列" in r.body


def test_a_question_is_never_mistaken_for_a_draft(session, project, tmp_path):
    """硬把整段回复塞进 body 的话，提问也会被当成需求稿，
    人还没回答就被推去确认。"""
    r = _draft_req(session, project)
    before = r.body
    out = _refine(session, r, _AskingAgent("这个是每人一套还是全公司一套？"), tmp_path)
    assert not out.get("ready") and r.body == before


def test_intake_stops_asking_after_three_rounds(session, project, tmp_path):
    """谈三轮还没成型就先出稿 —— 让人改比让人一直答问题强。"""
    from vplatform.core.models import Message
    r = _draft_req(session, project)
    for i in range(3):
        session.add(Message(project_id=project.id, requirement_id=r.id, role="agent",
                            author="ai", body=f"问题{i}？", stage="intake"))
        session.add(Message(project_id=project.id, requirement_id=r.id, role="user",
                            author="chen", body=f"答{i}", stage="intake"))
    session.flush()
    agent = _AskingAgent(DRAFT_REPLY)
    _refine(session, r, agent, tmp_path)
    assert "现在必须出需求稿" in agent.prompts[0]


def test_intake_without_agent_does_not_pretend_to_have_talked(session, project):
    r = _draft_req(session, project)
    out = _run(StageRunner(Capabilities(), session).refine_draft(r))
    assert out["skipped"] is True and out["ready"] is True
    assert "跳过对话" in _msgs(session, r)[-1].body


def test_intake_waits_while_a_question_is_unanswered(session, project, tmp_path):
    from vplatform.core.models import Message
    r = _draft_req(session, project)
    session.add(Message(project_id=project.id, requirement_id=r.id, role="agent",
                        author="ai", body="每人一套吗？", stage="intake",
                        awaiting_answer=True))
    session.flush()
    agent = _AskingAgent(DRAFT_REPLY)
    assert _refine(session, r, agent, tmp_path)["awaiting"] is True
    assert agent.prompts == []


@pytest.mark.parametrize("text", [
    "没有代码块",
    "```需求稿\n```",                       # 空块
    "```\n标题: 装成需求稿\n```",            # 没标 需求稿
])
def test_draft_parsing_rejects_non_drafts(text):
    from vplatform.orchestration.stages import parse_draft
    assert parse_draft(text) is None


def test_multi_repo_prompt_carries_the_repo_paths(session, project):
    """多仓时 cwd 是工位根（不是 git 仓），opencode 会把会话归到内置的
    `global` 项目、给不出任何 VCS 上下文 —— agent 只能靠 prompt 里的
    绝对路径找到代码。之前注释说「prompt 里说明各仓是子目录」，
    但 prompt 里根本没写。"""
    class WS:
        root = Path("/data/ws/plan")
        repos = {"api": "/data/ws/plan/api", "web": "/data/ws/plan/web"}

    m = StageRunner(Capabilities(), session).repo_map(WS())
    assert "/data/ws/plan/api" in m and "/data/ws/plan/web" in m
    assert "不是 git 仓" in m


def test_single_repo_needs_no_repo_map(session, project):
    """单仓时 cwd 就是仓目录，opencode 有完整上下文 —— 别塞废话。"""
    class WS:
        root = Path("/data/ws")
        repos = {"only": "/data/ws/only"}

    assert StageRunner(Capabilities(), session).repo_map(WS()) == ""


# ── 思考过程要推出去、也要留档 ──────────────────────────────────
class _StreamingAgent:
    """会边跑边回调事件的 agent 替身 —— 跟真实现同契约。"""

    def __init__(self, events, text=""):
        self.events, self.text = events, text

    async def create(self, *, cwd, title, parent=None):
        return parent or "ses_x"

    async def fork(self, sid, **kw):
        return sid

    async def send(self, sid, prompt, *, cwd, on_event=None, **kw):
        from vplatform.agents.session import AgentEvent, AgentReply
        for kind, text, data in self.events:
            if on_event:
                await on_event(AgentEvent(kind=kind, text=text, data=data))
        return AgentReply(session_id=sid or "ses_x", text=self.text)


EVENTS = [("tool", "读文件：exporter.py", {"tool": "read", "detail": "def export():"}),
          ("text", "看明白了", {})]


def test_thinking_is_streamed_while_the_agent_runs(session, project, req, tmp_path):
    """之前界面上只有一句「正在看代码…」，一等好几分钟 ——
    人不知道它在干嘛、干到哪了、还是已经卡死了。"""
    from vplatform.core.events import get_bus, reset_bus
    reset_bus()
    agent = _StreamingAgent(EVENTS, text="READY")
    _run(StageRunner(Capabilities(agent=agent, workspace=_NullWs(tmp_path)), session)
         .clarify(default_pipeline().get("clarify"), req))

    live = get_bus().live_backlog(f"req:{req.id}")
    # 实时推送要带上 text —— 跑的过程中人想看到它在说什么
    assert [e.payload["text"] for e in live] == ["读文件：exporter.py", "看明白了"]
    assert live[0].payload["tool"] == "read"
    assert all(e.ephemeral for e in live), "思考流不该落库"


def test_thinking_is_kept_on_the_message_for_later(session, project, req, tmp_path):
    """实时流只在内存里，进程重启就没了。刷新页面还要能展开看，
    所以落一份到消息上。"""
    from vplatform.core.events import reset_bus
    reset_bus()
    agent = _StreamingAgent(EVENTS, text="1. 是每人一套还是全公司一套？")
    _run(StageRunner(Capabilities(agent=agent, workspace=_NullWs(tmp_path)), session)
         .clarify(default_pipeline().get("clarify"), req))

    m = _msgs(session, req)[-1]
    # text 片段不落 trace —— 它已经拼成结论存在消息正文里了，
    # trace 再存一遍就是同一段话入库两次
    assert [t["text"] for t in m.trace] == ["读文件：exporter.py"]
    assert "看明白了" not in str(m.trace)


def test_a_new_run_does_not_reuse_the_previous_trace(session, project, req, tmp_path):
    """不清的话下一条消息会把上一次的思考再贴一遍。"""
    from vplatform.core.events import reset_bus
    reset_bus()
    runner = StageRunner(
        Capabilities(agent=_StreamingAgent(EVENTS, text="READY"),
                     workspace=_NullWs(tmp_path)), session)
    _run(runner.clarify(default_pipeline().get("clarify"), req))
    assert runner.take_trace() == [], "trace 取过没清"


def test_each_run_starts_with_a_clean_live_buffer(session, project, req, tmp_path):
    """上一次运行的残留不该混进这一次。"""
    from vplatform.core.events import get_bus, reset_bus
    reset_bus()
    get_bus().publish_live(stream=f"req:{req.id}", kind="agent_step",
                           payload={"text": "上一次的残留"})
    agent = _StreamingAgent(EVENTS, text="READY")
    _run(StageRunner(Capabilities(agent=agent, workspace=_NullWs(tmp_path)), session)
         .clarify(default_pipeline().get("clarify"), req))
    texts = [e.payload["text"] for e in get_bus().live_backlog(f"req:{req.id}")]
    assert "上一次的残留" not in texts


# ── 浏览器自检的行为 ────────────────────────────────────────────
def _preview_run(session, project, req, tmp_path, port=5111):
    from datetime import datetime, timedelta

    from vplatform.core.models import PortLease, Run, Task, Workspace
    t = Task(project_id=project.id, requirement_id=req.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    run = Run(project_id=project.id, task_id=t.id, branch="cr/1-t1", state="done")
    session.add(run); session.flush()
    d = tmp_path / "ws"; d.mkdir(exist_ok=True)
    session.add_all([
        Workspace(project_id=project.id, run_id=run.id, path=str(d), state="ready"),
        PortLease(project_id=project.id, port=port, workspace_id=run.id,
                  expires_at=datetime.utcnow() + timedelta(hours=1)),
    ])
    session.flush()
    return run


def _browser(session, req, agent, tmp_path):
    runner = StageRunner(Capabilities(agent=agent, workspace=_NullWs(tmp_path)),
                         session)
    return _run(runner.browser_check(default_pipeline().get("browser_check"), req))


def test_browser_check_says_skipped_not_passed_when_not_installed(
        session, project, req, tmp_path, monkeypatch):
    """**没检查不能说成检查过了。**"""
    _preview_run(session, project, req, tmp_path)
    monkeypatch.setattr("shutil.which", lambda _b: None)
    # 本机真装了 ego lite 的话，~/.local/bin 那条兜底会找到它 ——
    # 把 HOME 也指到空目录，才是真的「没装」
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "empty-home"))
    out = _browser(session, req, _AskingAgent("PASS"), tmp_path)
    assert out.ok and out.data["skipped"] is True
    assert "跳过" in out.detail


def test_browser_check_needs_a_preview_url(session, project, req, tmp_path,
                                           monkeypatch):
    """预览没起来就没得点 —— 别假装点过。"""
    monkeypatch.setattr("shutil.which", lambda _b: "/usr/local/bin/ego-browser")
    out = _browser(session, req, _AskingAgent("PASS"), tmp_path)
    assert out.ok and out.data["skipped"] is True
    assert "预览地址" in out.detail


def test_browser_check_blocks_on_a_serious_finding(session, project, req,
                                                   tmp_path, monkeypatch):
    """白屏这种问题放过去，就等于让人工审核去发现白屏。"""
    _preview_run(session, project, req, tmp_path)
    monkeypatch.setattr("shutil.which", lambda _b: "/usr/local/bin/ego-browser")
    agent = _AskingAgent("[严重] 打开 /orders 后整页空白 → 控制台 500\n"
                         "[一般] 导出按钮没有 loading 态")
    out = _browser(session, req, agent, tmp_path)
    assert out.ok is False
    assert len(out.data["findings"]) == 2


def test_browser_check_passes_with_only_minor_findings(session, project, req,
                                                       tmp_path, monkeypatch):
    _preview_run(session, project, req, tmp_path)
    monkeypatch.setattr("shutil.which", lambda _b: "/usr/local/bin/ego-browser")
    out = _browser(session, req, _AskingAgent("[一般] 按钮没有 loading 态"),
                   tmp_path)
    assert out.ok and len(out.data["findings"]) == 1


def test_browser_check_tells_the_agent_the_preview_url(session, project, req,
                                                       tmp_path, monkeypatch):
    """不告诉它地址，它就只能瞎点。"""
    _preview_run(session, project, req, tmp_path, port=5123)
    monkeypatch.setattr("shutil.which", lambda _b: "/usr/local/bin/ego-browser")
    agent = _AskingAgent("PASS")
    _browser(session, req, agent, tmp_path)
    assert "127.0.0.1:5123" in agent.prompts[0]


def test_browser_findings_land_in_the_conversation(session, project, req,
                                                   tmp_path, monkeypatch):
    """结论要留在对话里 —— 审核的人得看得到它点出了什么。"""
    _preview_run(session, project, req, tmp_path)
    monkeypatch.setattr("shutil.which", lambda _b: "/usr/local/bin/ego-browser")
    _browser(session, req, _AskingAgent("[严重] /orders 白屏 → 控制台 500"), tmp_path)
    assert "白屏" in _msgs(session, req)[-1].body


def test_browser_bin_found_outside_path(monkeypatch, tmp_path):
    """**只查 PATH 不够。**

    ego lite 把命令装在 `~/.local/bin`，而 worker 是后台进程 ——
    launchd / systemd / nohup 起来的拿到的是精简 PATH，通常不含它。
    只查 PATH 的话：终端里 `command -v ego-browser` 有，平台却报「没装」，
    排查的人会去怀疑安装本身。
    """
    from vplatform.orchestration.stages import browser_available, browser_bin

    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    fake = home / ".local" / "bin" / "ego-browser"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)

    monkeypatch.setattr("shutil.which", lambda _b: None)      # PATH 里没有
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    assert browser_bin() == str(fake)
    assert browser_available() is True


def test_browser_bin_prefers_path(monkeypatch):
    """PATH 里有就用 PATH 的 —— 用户自己换过位置要认。"""
    from vplatform.orchestration.stages import browser_bin

    monkeypatch.setattr("shutil.which", lambda _b: "/opt/homebrew/bin/ego-browser")
    assert browser_bin() == "/opt/homebrew/bin/ego-browser"


def test_the_agent_is_told_the_browser_path(session, project, req, tmp_path,
                                            monkeypatch):
    """精简 PATH 下 agent 自己也找不到，得把绝对路径给它。"""
    _preview_run(session, project, req, tmp_path)
    monkeypatch.setattr("shutil.which", lambda _b: "/opt/ego/ego-browser")
    agent = _AskingAgent("PASS")
    _browser(session, req, agent, tmp_path)
    assert "/opt/ego/ego-browser" in agent.prompts[0]


def test_agent_call_does_not_hold_the_transaction(session, project, req, tmp_path):
    """**agent 跑几分钟，事务不能开几分钟。**

    worker 是 `with session_scope() as s: await handler(ctx)` —— 事务包住整个
    handler。不在调 agent 前提交的话，这几分钟里 MySQL 一直握着前面写过的
    行锁；用户在页面上发一句话，API 插 messages 就等锁等到
    `Lock wait timeout exceeded`，前端显示 Internal Server Error。
    实测用户就是这么撞上的。
    """
    committed = {"n": 0}
    real_commit = session.commit

    def spy():
        committed["n"] += 1
        return real_commit()

    session.commit = spy

    class WatchAgent(_AskingAgent):
        async def send(self, sid, prompt, **kw):
            # agent 被调到的那一刻，前面的写必须已经提交
            assert committed["n"] > 0, "调 agent 前没提交，锁会被握住整轮"
            return await super().send(sid, prompt, **kw)

    _run(StageRunner(Capabilities(agent=WatchAgent("READY"),
                                  workspace=_NullWs(tmp_path)), session)
         .clarify(default_pipeline().get("clarify"), req))
    session.commit = real_commit


def test_releasing_a_workspace_also_stops_its_opencode_server(session, project,
                                                              req, tmp_path):
    """serve 必须在工位目录里起，所以一个工位一个进程。工位释放时不关的话，
    并行需求一多就是几十个常驻 server，端口和内存都会被吃光。"""
    closed = []

    class Pool:
        async def close(self, cwd=""):
            closed.append(cwd)

    class Agent(_AskingAgent):
        pool = Pool()

    runner = StageRunner(
        Capabilities(agent=Agent("READY"), workspace=_NullWs(tmp_path)), session)
    _run(runner.clarify(default_pipeline().get("clarify"), req))
    assert closed, "工位释放了但 server 没关"


def test_a_failing_server_close_does_not_break_release(session, project, req,
                                                       tmp_path):
    """关 server 失败不能把工位释放也带走 —— 工位不释放才是真泄漏。"""
    class Pool:
        async def close(self, cwd=""):
            raise RuntimeError("server 已经死了")

    class Agent(_AskingAgent):
        pool = Pool()

    out = _run(StageRunner(Capabilities(agent=Agent("READY"),
                                        workspace=_NullWs(tmp_path)), session)
               .clarify(default_pipeline().get("clarify"), req))
    assert out.ok


def test_trace_merges_token_deltas_before_persisting(session, project, req,
                                                     tmp_path):
    """opencode 逐 token 推，一轮 1500+ 条增量。原样入库就是个巨大的
    JSON 数组，读写都慢，页面也没法用 —— 同一个 part 的要拼成一段。"""
    from vplatform.core.events import reset_bus
    reset_bus()
    deltas = [("reasoning", t, {"part_id": "p1", "delta": True})
              for t in ("我", "先看", "导出")]
    deltas.append(("tool", "读文件：a.py", {"tool": "read"}))
    agent = _StreamingAgent(deltas, text="READY")

    _run(StageRunner(Capabilities(agent=agent, workspace=_NullWs(tmp_path)),
                     session).clarify(default_pipeline().get("clarify"), req))
    tr = _msgs(session, req)[-1].trace
    assert [t["text"] for t in tr] == ["我先看导出", "读文件：a.py"]


def test_intake_honours_the_skip_button(session, project, tmp_path):
    """**「✓ 够了直接干」在立需求页上必须管用。**

    之前只有 clarify 认这个标记，立需求页那个按钮点了等于没点 ——
    AI 接着问下一轮，业务员被困在问答里出不来。
    """
    from vplatform.core.models import Message

    r = _draft_req(session, project)
    session.add(Message(project_id=project.id, requirement_id=r.id, role="user",
                        author="chen", body="✓ 够了直接干\n按你的判断来",
                        stage="intake"))
    session.flush()
    agent = _AskingAgent(DRAFT_REPLY)
    out = _refine(session, r, agent, tmp_path)
    assert out.get("ready") is True
    assert "现在必须出需求稿" in agent.prompts[0], "没强制出稿，还在问问题"


def test_commit_detection_falls_back_when_integration_branch_is_missing(
        session, project, req):
    """**集成分支不一定存在，下游不能假设它存在。**

    实测代价极高：agent 把功能写完、测试写完、377 个后端测试跑通、
    commit 也提了，平台却跑 `rev-list vibe/dev..cr/12-t1` —— 这个 ref 在
    那个仓里不存在，命令 fatal，于是判定「没有产生任何 commit」，
    **把这份真实工作整个扔掉**。
    """
    seen = []

    class Prov:
        async def exec(self, ws, argv, **kw):
            from vplatform.workspace.provider import ExecResult
            seen.append(argv)
            if argv[:3] == ["git", "rev-parse", "--verify"]:
                ref = argv[-1]
                # 这个仓只有 origin/main，没有 vibe/dev
                return (ExecResult(0, "abc123", "") if ref == "origin/main"
                        else ExecResult(1, "", ""))
            if "rev-list" in argv:
                return ExecResult(0, "1", "")
            return ExecResult(0, "sha1", "")

    class WS:
        repos = {"api": "/w/api"}

    out = _run(StageRunner(Capabilities(workspace=Prov()), session)
               ._collect_commits(WS(), "cr/12-t1", base="vibe/dev"))
    assert out == {"api": "sha1"}, "真实 commit 被丢掉了"
    assert any("origin/main..cr/12-t1" in a for argv in seen for a in argv), \
        "没有退到仓自己的主干"


def test_a_session_from_another_workspace_is_not_reused(session, project, req,
                                                        tmp_path):
    """**会话绑工作目录，跨目录复用会静默失败。**

    serve 模式下每个工位一个 server。往一个「别的目录的 server 建的」会话
    发 prompt —— 服务端照样返回 204，然后**什么都不做**：事件流一条没有，
    任务跑到超时，日志里看不出任何原因。重试换了工位就必现。
    """
    from vplatform.core.models import AgentSession as Row

    session.add(Row(project_id=project.id, requirement_id=req.id, task_id=None,
                    purpose="clarify", session_id="ses_old", cwd="/旧工位"))
    session.flush()

    runner = StageRunner(Capabilities(agent=_StubAgent()), session)
    sid, fork, known = _run(runner._session_for(req, purpose="clarify",
                                                cwd="/新工位"))
    assert sid != "ses_old", "复用了别的目录的会话"
    assert known is None


def test_same_workspace_still_reuses_the_session(session, project, req):
    """同一个工位要接着聊 —— 别把「续改复用」也砍了。"""
    from vplatform.core.models import AgentSession as Row

    session.add(Row(project_id=project.id, requirement_id=req.id, task_id=None,
                    purpose="clarify", session_id="ses_keep", cwd="/同一个工位"))
    session.flush()

    runner = StageRunner(Capabilities(agent=_StubAgent()), session)
    sid, fork, known = _run(runner._session_for(req, purpose="clarify",
                                                cwd="/同一个工位"))
    assert sid == "ses_keep" and known == "ses_keep"


def test_browser_check_does_not_pass_when_it_could_not_connect(
        session, project, req, tmp_path, monkeypatch):
    """**连不上就不是「通过」。**

    agent 说「端口没进程监听 / 连接被拒绝」时，它什么都没检查成；
    只看有没有 `[严重]` 条目的话，这种情况会被判通过 —— 实测撞到过：
    一条根本没被点过的需求带着「浏览器自检通过」进了人工审核。
    """
    _preview_run(session, project, req, tmp_path)
    monkeypatch.setattr("shutil.which", lambda _b: "/usr/local/bin/ego-browser")
    agent = _AskingAgent("预览环境 `http://127.0.0.1:5101` 并未启动，"
                         "端口 5101 无进程监听，连接被拒绝。")
    out = _browser(session, req, agent, tmp_path)
    assert out.data.get("unreachable") is True
    assert out.data.get("skipped") is True
    assert "不是通过" in out.detail


def test_preview_does_not_claim_ready_when_nothing_listens(session, project,
                                                           req, tmp_path):
    """**租到端口 ≠ 有服务在跑。**

    不探测就报「预览就绪」的话，业务员点开是空白页，
    浏览器自检也会对着一个连不上的地址「通过」。
    """
    _preview_run(session, project, req, tmp_path, port=59999)   # 没人监听
    out = _run(StageRunner(Capabilities(), session)
               .preview(default_pipeline().get("preview"), req))
    assert out.ok
    assert out.data.get("serving") is False
    assert out.data["previews"] == {}
    assert "没有服务在监听" in out.detail


def test_prompts_ask_the_model_to_think_in_chinese(session, project, tmp_path):
    """**思考过程直接流到页面上给业务员看。**

    模型默认用英文推理（实测 deepseek 的 reasoning 全是英文），
    看不懂的思考等于没有思考。
    """
    from vplatform.orchestration.dag import Stage as S
    from vplatform.orchestration.handlers import skill_prompt

    p = skill_prompt(S(key="clarify", spec={"skill": "grilling"}), context="随便")
    assert "用中文思考" in p and "reasoning" in p

    r = _draft_req(session, project)
    agent = _AskingAgent(DRAFT_REPLY)
    _refine(session, r, agent, tmp_path)
    assert "用中文思考" in agent.prompts[0], "立需求那条 prompt 漏了"
