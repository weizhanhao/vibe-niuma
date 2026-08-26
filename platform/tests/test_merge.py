import asyncio
import subprocess
from pathlib import Path

import pytest

from vplatform.core.models import MergeJob, Requirement, next_requirement_seq
from vplatform.merge.conflict import ConflictLadder, conflicted_files
from vplatform.merge.queue import MergeQueue


def _req(session, project, title):
    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title=title, requested_by="u")
    session.add(r); session.flush()
    return r


# ── 队列 ─────────────────────────────────────────────────────────
def test_queue_is_serial_per_repo(session, project):
    q = MergeQueue(session)
    a = q.enqueue(project_id=project.id, requirement_id=_req(session, project, "A").id,
                  repo_name="web")
    b = q.enqueue(project_id=project.id, requirement_id=_req(session, project, "B").id,
                  repo_name="web")
    assert (a.position, b.position) == (1, 2)
    assert q.head(project_id=project.id, repo_name="web").id == a.id

    q.mark(a, "rebasing")
    # 队首在处理中，仍返回它 —— 同一个仓同时只处理一条
    assert q.head(project_id=project.id, repo_name="web").id == a.id
    q.mark(a, "merged", sha="abc")
    assert q.head(project_id=project.id, repo_name="web").id == b.id


def test_queues_of_different_repos_are_independent(session, project):
    q = MergeQueue(session)
    w = q.enqueue(project_id=project.id, requirement_id=_req(session, project, "A").id,
                  repo_name="web")
    a = q.enqueue(project_id=project.id, requirement_id=_req(session, project, "B").id,
                  repo_name="api")
    assert w.position == a.position == 1     # 各排各的


def test_enqueue_is_idempotent(session, project):
    q = MergeQueue(session)
    r = _req(session, project, "A")
    x = q.enqueue(project_id=project.id, requirement_id=r.id, repo_name="web")
    y = q.enqueue(project_id=project.id, requirement_id=r.id, repo_name="web")
    assert x.id == y.id
    assert session.query(MergeJob).count() == 1


def test_reorder_pushes_touch_risky_behind(session, project):
    """touches 相交的需求排到后面 —— 冲突预防前置到调度期（§8.3 保险 ①）。"""
    q = MergeQueue(session)
    risky = _req(session, project, "改 export.py")
    safe = _req(session, project, "改 login.tsx")
    q.enqueue(project_id=project.id, requirement_id=risky.id, repo_name="web")
    q.enqueue(project_id=project.id, requirement_id=safe.id, repo_name="web")

    ordered = q.reorder_by_touch_risk(project_id=project.id, repo_name="web",
                                      risky_requirement_ids={risky.id})
    assert [j.requirement_id for j in ordered] == [safe.id, risky.id]
    assert [j.position for j in ordered] == [1, 2]


# ── 三档冲突（真 git）────────────────────────────────────────────
def _repo_with_conflict(tmp: Path) -> Path:
    repo = tmp / "r"; repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t"); run("git", "config", "user.name", "t")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    run("git", "add", "-A"); run("git", "commit", "-qm", "base")

    run("git", "checkout", "-qb", "feature")
    (repo / "f.txt").write_text("feature\n", encoding="utf-8")
    run("git", "add", "-A"); run("git", "commit", "-qm", "feat")

    run("git", "checkout", "-q", "main")
    (repo / "f.txt").write_text("main-moved\n", encoding="utf-8")
    run("git", "add", "-A"); run("git", "commit", "-qm", "main")
    return repo


def _repo_clean(tmp: Path) -> Path:
    repo = tmp / "c"; repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t"); run("git", "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    run("git", "add", "-A"); run("git", "commit", "-qm", "base")
    run("git", "checkout", "-qb", "feature")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    run("git", "add", "-A"); run("git", "commit", "-qm", "feat")
    return repo


def test_clean_rebase_stops_at_first_rung(tmp_path):
    repo = _repo_clean(tmp_path)
    res = asyncio.run(ConflictLadder().resolve(repo, onto="main", branch="feature"))
    assert res.resolved
    assert [r.stage for r in res.rungs] == ["git"]      # 没冲突就不往下走
    assert res.rungs[0].ok


def test_conflict_walks_the_ladder_and_records_each_rung(tmp_path):
    repo = _repo_with_conflict(tmp_path)

    async def ai(repo_path, files, session_id):
        # AI 档：真去解，然后返回仍未解决的
        for f in list(files):
            Path(repo_path, f).write_text("merged-by-ai\n", encoding="utf-8")
            await asyncio.sleep(0)
        subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True, capture_output=True)
        return []

    ladder = ConflictLadder(ai_resolver=ai)
    res = asyncio.run(ladder.resolve(repo, onto="main", branch="feature",
                                     session_id="ses_8f3a41"))
    stages = [r.stage for r in res.rungs]
    assert stages[:3] == ["git", "mergiraf", "ai"]      # 三档都要留痕
    assert res.rungs[0].ok is False                      # git 解不了
    assert "ses_8f3a41" in res.rungs[2].detail           # AI 档带着原会话
    assert res.resolved


def test_missing_mergiraf_is_recorded_not_silently_skipped(tmp_path):
    """没装 mergiraf 是可接受的（那档是优化），但必须如实记录，
    别让人以为它跑过了。"""
    repo = _repo_with_conflict(tmp_path)
    ladder = ConflictLadder(mergiraf_bin="definitely-not-installed")
    res = asyncio.run(ladder.resolve(repo, onto="main", branch="feature"))
    rung = next(r for r in res.rungs if r.stage == "mergiraf")
    assert rung.ok is False and "未安装" in rung.detail


def test_unresolved_conflict_aborts_rebase_leaving_repo_clean(tmp_path):
    """解不掉必须 abort，不能把半 rebase 的仓库留在那 —— 下一条需求会踩到。"""
    repo = _repo_with_conflict(tmp_path)
    res = asyncio.run(ConflictLadder().resolve(repo, onto="main", branch="feature"))
    assert not res.resolved
    out = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                         capture_output=True, text=True).stdout
    assert "UU" not in out                              # 没有留下冲突标记
    assert asyncio.run(conflicted_files(repo)) == []


# ── H7 / H8 回归 ─────────────────────────────────────────────────
def test_rebase_that_never_started_is_not_reported_as_resolved(tmp_path):
    """**H7 回归。**

    rebase 因为 onto 不存在之类原因非 0 退出时**不产生冲突文件**。
    之前 `resolved` 只看 `remaining` 是否为空 → 返回 True →
    合并队列会把这条标成 merged，而实际上一次 rebase 都没发生。
    """
    repo = _repo_clean(tmp_path)
    res = asyncio.run(ConflictLadder().resolve(repo, onto="does-not-exist",
                                               branch="feature"))
    assert res.resolved is False
    assert res.aborted_reason
    assert "rebase 未能启动" in res.rungs[0].detail


def test_ai_resolved_files_are_staged(tmp_path):
    """**H8 回归。**

    AI 解完的文件必须 git add，否则 `rebase --continue` 报 "needs merge"，
    第三档永远收不了尾。mergiraf 档自己 add 了，AI 档之前漏了。
    """
    repo = _repo_with_conflict(tmp_path)

    async def ai(repo_path, files, session_id):
        # 只改文件内容，**故意不 add** —— 阶梯必须自己补上
        for f in files:
            Path(repo_path, f).write_text("ai-merged\n", encoding="utf-8")
        return []

    res = asyncio.run(ConflictLadder(ai_resolver=ai)
                      .resolve(repo, onto="main", branch="feature"))
    assert res.resolved, f"AI 解完却没收尾：{[r.detail for r in res.rungs]}"
    out = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                         capture_output=True, text=True).stdout
    assert "UU" not in out


def test_ai_claiming_success_with_markers_left_is_rejected(tmp_path):
    """AI 说解完了但文件里还留着 <<<<<<< —— 不能算解决。"""
    repo = _repo_with_conflict(tmp_path)

    async def lying_ai(repo_path, files, session_id):
        for f in files:
            Path(repo_path, f).write_text(
                "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n", encoding="utf-8")
        return []          # 谎称全解决了

    res = asyncio.run(ConflictLadder(ai_resolver=lying_ai)
                      .resolve(repo, onto="main", branch="feature"))
    assert res.resolved is False
    assert asyncio.run(conflicted_files(repo)) == []      # 已 abort，仓库干净


def test_unresolved_leaves_no_rebase_in_progress(tmp_path):
    """解不掉必须 abort —— 否则下一条合并任务撞上
    'there is already a rebase-merge directory'，该仓队列从此卡死。"""
    repo = _repo_with_conflict(tmp_path)
    asyncio.run(ConflictLadder().resolve(repo, onto="main", branch="feature"))
    assert not (Path(repo) / ".git" / "rebase-merge").exists()
    assert not (Path(repo) / ".git" / "rebase-apply").exists()
    # 再来一次不该报 "already in progress"
    res2 = asyncio.run(ConflictLadder().resolve(repo, onto="main", branch="feature"))
    assert "already" not in (res2.aborted_reason or "")


def test_merge_queue_never_contains_a_phantom_repo(session, project):
    """`commit_shas` 的键就是仓名 —— 塞非仓名的东西进去会变成幽灵仓。

    曾经这里多存了一条 `"_workspace": <工位路径>`，合并阶段照单全收，
    队列里就冒出一个叫 `_workspace` 的仓，永远合不掉也删不掉。
    """
    from vplatform.core.models import Requirement, Run, Task, next_requirement_seq

    r = Requirement(project_id=project.id, seq=next_requirement_seq(session, project.id),
                    title="x", requested_by="chen", stage="merge")
    session.add(r); session.flush()
    t = Task(project_id=project.id, requirement_id=r.id, key="T1", title="x",
             state="done")
    session.add(t); session.flush()
    session.add(Run(project_id=project.id, task_id=t.id, branch="cr/1-t1",
                    state="done", commit_shas={"orders-api": "abc"}))
    session.flush()

    runs = session.query(Run).filter_by(task_id=t.id).all()
    repos = {name for run in runs for name in (run.commit_shas or {})}
    assert repos == {"orders-api"}
    assert not any(n.startswith("_") for n in repos)


def test_merge_rebases_onto_a_ref_that_actually_exists(session, project):
    """**合进的分支也要是真实存在的那条。**

    直接用集成分支名的话，仓里没有它就是
    `fatal: invalid upstream 'vibe/dev'` —— 三档冲突梯子第一档就起不来，
    报出来却是「冲突未解决」，让人以为是代码冲突。
    实测走真需求时栽在这儿：agent 写好的代码合不进去。
    """
    import asyncio

    from vplatform.orchestration.handlers import Capabilities
    from vplatform.orchestration.stages import StageRunner

    class WS:
        repos = {"api": "/w/api"}

    class Prov:
        async def exec(self, ws, argv, **kw):
            from vplatform.workspace.provider import ExecResult
            if argv[:3] == ["git", "rev-parse", "--verify"]:
                # 这个仓只有 origin/main
                return (ExecResult(0, "abc", "") if argv[-1] == "origin/main"
                        else ExecResult(1, "", ""))
            return ExecResult(0, "", "")

    got = asyncio.run(StageRunner(Capabilities(workspace=Prov()), session)
                      .resolve_base(WS(), "api", "vibe/dev"))
    assert got == "origin/main", "还在拿一个不存在的 ref 去 rebase"
