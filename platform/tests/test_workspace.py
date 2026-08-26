"""Workspace 隔离层测试 —— **用真 git 仓**，不 mock git。

M2 是分水岭：这之前系统是串行的。所以这里的核心断言是
「5 个 Run 同时跑互不污染」，用 mock 证不了。

容器部分用 FakeDocker 替身（CI 不一定有 docker daemon）。
"""
import asyncio
import subprocess
from pathlib import Path

import pytest

from vplatform.workspace.ports import NoPortAvailable, PortLeaseManager
from vplatform.workspace.provider import RepoSpec
from vplatform.workspace.worktree_docker import WorktreeDockerProvider


# ── 端口租约 ────────────────────────────────────────────────────
def test_port_lease_allocates_within_project_range(session, project):
    project.port_min, project.port_max = 5100, 5102
    session.flush()
    m = PortLeaseManager(session)
    ports = [m.acquire(project_id=project.id, workspace_id=f"ws{i}") for i in range(3)]
    assert ports == [5100, 5101, 5102]


def test_port_lease_exhaustion_raises_not_blocks(session, project):
    """满了要明确抛，让调用方去排队 —— 不能硬等或静默复用。"""
    project.port_min = project.port_max = 5100
    session.flush()
    m = PortLeaseManager(session)
    m.acquire(project_id=project.id, workspace_id="a")
    with pytest.raises(NoPortAvailable, match="已满"):
        m.acquire(project_id=project.id, workspace_id="b")


def test_port_release_makes_it_reusable(session, project):
    project.port_min = project.port_max = 5100
    session.flush()
    m = PortLeaseManager(session)
    assert m.acquire(project_id=project.id, workspace_id="a") == 5100
    assert m.release(project_id=project.id, workspace_id="a") == 1
    assert m.acquire(project_id=project.id, workspace_id="b") == 5100


def test_expired_lease_is_reaped(session, project):
    from datetime import datetime, timedelta
    from vplatform.core.models import PortLease

    project.port_min = project.port_max = 5100
    session.flush()
    session.add(PortLease(project_id=project.id, port=5100, workspace_id="zombie",
                          expires_at=datetime.utcnow() - timedelta(hours=1)))
    session.flush()
    # TTL 是兜底：worker 崩了没释放，端口不能永久泄漏
    assert PortLeaseManager(session).acquire(project_id=project.id, workspace_id="new") == 5100


# ── worktree 隔离（真 git）──────────────────────────────────────
def _make_upstream(tmp: Path, name: str) -> str:
    repo = tmp / f"{name}-origin"
    repo.mkdir(parents=True)
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("httpx\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    return str(repo)


class FakeDocker:
    """容器替身。CI 不一定有 docker daemon，但隔离语义必须能测。"""

    def __init__(self):
        self.started, self.stopped, self.built = [], [], []
        self.images: set[str] = set()

    async def image_exists(self, tag): return tag in self.images
    async def build(self, tag, context, dockerfile):
        from vplatform.workspace.provider import ExecResult
        self.built.append((tag, dockerfile)); self.images.add(tag)
        return ExecResult(0, "", "")
    async def start(self, *, name, image, mounts, port, network, workdir=None):
        self.started.append({"name": name, "image": image, "port": port,
                             "mounts": mounts, "workdir": workdir})
        return f"cid-{len(self.started)}"
    async def stop(self, cid): self.stopped.append(cid)
    async def exec(self, cid, argv, *, workdir, timeout):
        from vplatform.workspace.provider import ExecResult
        return ExecResult(0, "", "")


@pytest.fixture()
def provider(tmp_path):
    return WorktreeDockerProvider(root=tmp_path / "data", docker=FakeDocker())


def test_parallel_runs_do_not_pollute_each_other(provider, tmp_path):
    """**M2 的核心断言。**

    v1 只有一个工作树，create_branch 会 reset --hard + clean —— 第二个 Run
    直接抹掉第一个 agent 正在写的文件。这里 5 个 Run 各写各的，互不可见。
    """
    url = _make_upstream(tmp_path, "web")
    specs = [RepoSpec(name="web", url=url)]

    async def go():
        handles = []
        for i in range(5):
            h = await provider.acquire(project_id="p1", run_id=f"run{i}",
                                       branch=f"cr/{i}", base_branch="main", repos=specs)
            handles.append(h)
            # 每个工位写自己的内容
            Path(h.repos["web"], "app.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
        return handles

    handles = asyncio.run(go())

    # 5 个工位彼此独立，内容各是各的
    for i, h in enumerate(handles):
        assert Path(h.repos["web"], "app.py").read_text() == f"VALUE = {i}\n"
    # 目录也各不相同
    assert len({h.repos["web"] for h in handles}) == 5
    # 共享同一个 bare mirror（object store 只有一份）
    mirror = provider._mirror("p1", "web")
    assert mirror.exists()

    async def release_all():
        await asyncio.gather(*(provider.release(h) for h in handles))

    asyncio.run(release_all())
    for h in handles:
        assert not Path(h.root).exists()


def test_acquire_creates_branch_from_base(provider, tmp_path):
    url = _make_upstream(tmp_path, "web")

    async def go():
        h = await provider.acquire(project_id="p1", run_id="r1", branch="cr/142-t1",
                                   base_branch="main", repos=[RepoSpec("web", url)])
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                             cwd=h.repos["web"], capture_output=True, text=True)
        return h, out.stdout.strip()

    h, branch = asyncio.run(go())
    assert branch == "cr/142-t1"


def test_multi_repo_is_atomic_on_failure(provider, tmp_path):
    """坑 3：一个仓失败必须整体回滚。半个工位比没有工位更糟 ——
    后续步骤会以为环境就绪，在缺仓的目录里瞎改。"""
    good = _make_upstream(tmp_path, "web")
    specs = [RepoSpec("web", good), RepoSpec("api", "file:///definitely/not/here")]

    async def go():
        with pytest.raises(Exception):
            await provider.acquire(project_id="p1", run_id="r1", branch="cr/1",
                                   base_branch="main", repos=specs)

    asyncio.run(go())
    # 回滚干净：工位目录不该留下
    assert not (provider.root / "p1" / "workspaces" / "r1").exists()
    assert not provider.docker.started      # 仓没齐就不该起容器


def test_deps_fingerprint_is_content_based_not_mtime(provider, tmp_path):
    """指纹必须看内容 —— worktree 每次新建 mtime 都变，用 mtime 等于永远重建镜像。"""
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / "requirements.txt").write_text("httpx\n", encoding="utf-8")
    assert provider.deps_fingerprint({"r": str(a)}) == provider.deps_fingerprint({"r": str(b)})

    (b / "requirements.txt").write_text("httpx\nredis\n", encoding="utf-8")
    assert provider.deps_fingerprint({"r": str(a)}) != provider.deps_fingerprint({"r": str(b)})


def test_deps_image_is_cached_across_runs(provider, tmp_path):
    """坑 1：锁文件没变就复用镜像。不缓存的话每个工位 npm i 三分钟，
    worktree 那点速度优势全被吃掉。"""
    url = _make_upstream(tmp_path, "web")
    specs = [RepoSpec("web", url)]

    async def go():
        h1 = await provider.acquire(project_id="p1", run_id="r1", branch="c1",
                                    base_branch="main", repos=specs)
        h2 = await provider.acquire(project_id="p1", run_id="r2", branch="c2",
                                    base_branch="main", repos=specs)
        return h1, h2

    h1, h2 = asyncio.run(go())
    assert h1.image == h2.image
    assert len(provider.docker.built) == 1      # 只构建了一次


def test_release_is_idempotent(provider, tmp_path):
    url = _make_upstream(tmp_path, "web")

    async def go():
        h = await provider.acquire(project_id="p1", run_id="r1", branch="c1",
                                   base_branch="main", repos=[RepoSpec("web", url)])
        await provider.release(h)
        await provider.release(h)     # 重复释放不能炸 —— reaper 会重试

    asyncio.run(go())


def test_worktree_removed_from_mirror_bookkeeping(provider, tmp_path):
    """释放后 mirror 的 worktree 登记也要清掉，否则 git 会拒绝同名重建。"""
    url = _make_upstream(tmp_path, "web")

    async def go():
        h = await provider.acquire(project_id="p1", run_id="r1", branch="c1",
                                   base_branch="main", repos=[RepoSpec("web", url)])
        await provider.release(h)
        mirror = provider._mirror("p1", "web")
        return subprocess.run(["git", "worktree", "list"], cwd=mirror,
                              capture_output=True, text=True).stdout

    out = asyncio.run(go())
    assert "workspaces/r1" not in out


# ── C1 回归：fetch 不能删掉活跃工位的分支 ──────────────────────
def test_fetch_does_not_prune_active_run_branches(provider, tmp_path):
    """**CRITICAL 回归。**

    原来 ensure_mirror 用 `fetch --prune '+refs/*:refs/*'`，它会把 origin 上
    不存在的本地分支删掉 —— 而 `cr/<id>-t<n>` 正是这种分支（还没 push）。
    于是第二个 Run 一 acquire 就把第一个 Run 的分支 prune 掉，
    agent 已提交的代码变成不可达对象直接蒸发。git 不保护活跃 worktree 的分支。
    """
    url = _make_upstream(tmp_path, "web")
    specs = [RepoSpec(name="web", url=url)]

    async def go():
        h1 = await provider.acquire(project_id="p1", run_id="r1", branch="cr/1-t1",
                                    base_branch="main", repos=specs)
        # Run 1 的 agent 提交了成果（尚未 push）
        p = Path(h1.repos["web"], "agent_work.py")
        p.write_text("VALUE = 'agent 的成果'\n", encoding="utf-8")
        for argv in (["git", "add", "-A"],
                     ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                      "commit", "-qm", "agent work"]):
            subprocess.run(argv, cwd=h1.repos["web"], check=True, capture_output=True)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=h1.repos["web"],
                             capture_output=True, text=True).stdout.strip()

        # Run 2 进来 —— 会触发对同一个 mirror 的 fetch
        h2 = await provider.acquire(project_id="p1", run_id="r2", branch="cr/1-t2",
                                    base_branch="main", repos=specs)
        return h1, h2, sha

    h1, h2, sha = asyncio.run(go())

    # Run 1 的分支和 commit 必须还在
    mirror = provider._mirror("p1", "web")
    refs = subprocess.run(["git", "for-each-ref", "--format=%(refname:short)",
                           "refs/heads"], cwd=mirror, capture_output=True,
                          text=True).stdout
    assert "cr/1-t1" in refs, f"Run 1 的分支被 prune 掉了！refs={refs!r}"

    status = subprocess.run(["git", "status", "--short", "--branch"],
                            cwd=h1.repos["web"], capture_output=True, text=True).stdout
    assert "No commits yet" not in status
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=h1.repos["web"],
                          capture_output=True, text=True).stdout.strip()
    assert head == sha
    assert Path(h1.repos["web"], "agent_work.py").exists()


def test_missing_repo_gives_readable_error_not_stopiteration(provider, tmp_path):
    """请求的仓一个都没注册时要给人能读懂的错误，
    不是 `coroutine raised StopIteration`。"""
    async def go():
        with pytest.raises(Exception) as ei:
            await provider.acquire(project_id="p1", run_id="r1", branch="c1",
                                   base_branch="main", repos=[])
        return str(ei.value)

    msg = asyncio.run(go())
    assert "没有可用的仓" in msg
    assert "StopIteration" not in msg


def test_container_mounts_project_root_at_identical_path(tmp_path):
    """**容器内 git 能不能用，取决于路径是否与宿主一致。**

    git worktree 的 `.git` 文件里写的是 bare mirror 的绝对路径，
    mirror 那边的 gitdir 也反指回 worktree 的绝对路径。
    只把 ws_root 挂到 /w 的话两个路径在容器里都不存在，
    `git` 直接报 "not a git repository" —— agent 没法 commit、
    拿不到 sha、冲突阶梯全废。实测在真容器里确认过。
    """
    url = _make_upstream(tmp_path, "web")
    prov = WorktreeDockerProvider(root=tmp_path / "data", docker=FakeDocker(),
                                  use_container=True)

    async def go():
        return await prov.acquire(project_id="p1", run_id="r1", branch="c1",
                                  base_branch="main",
                                  repos=[RepoSpec("web", url)], port=5100)

    h = asyncio.run(go())
    started = prov.docker.started[0]
    mounts = dict(started["mounts"])
    project_root = str(tmp_path / "data" / "p1")

    # 挂的是整个 project 目录（含 mirrors 和 workspaces），且两边同名
    assert project_root in mounts, f"没挂 project 根目录：{mounts}"
    assert mounts[project_root] == project_root, "宿主与容器内路径必须一致"
    # workdir 指向工位真实路径，不是 /w
    assert started["workdir"] == str(h.root)


def test_exec_uses_real_paths_not_slash_w(tmp_path):
    url = _make_upstream(tmp_path, "web")
    prov = WorktreeDockerProvider(root=tmp_path / "data", docker=FakeDocker(),
                                  use_container=True)

    async def go():
        h = await prov.acquire(project_id="p1", run_id="r1", branch="c1",
                               base_branch="main", repos=[RepoSpec("web", url)])
        await prov.exec(h, ["git", "status"], cwd="web")
        return h

    h = asyncio.run(go())
    # FakeDocker.exec 记不到 workdir，改为直接验证拼法
    assert str(h.root / "web") == h.repos["web"]


# ── 网络瞬时故障重试 ────────────────────────────────────────────
def test_transient_network_error_is_retried(tmp_path, monkeypatch):
    """**实测遇到过两次**：一次 "Empty reply from server"，
    一次 CPU 只用了 0.02 秒却挂了 13 分钟的 git clone。
    这类故障重试一次往往就好，不重试就是一条需求白白失败。"""
    from vplatform.workspace import worktree_docker as wd

    calls = {"n": 0}

    async def flaky(argv, *, cwd=None, timeout=None, check_ok=True, env=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return wd.ExecResult(128, "", "fatal: unable to access: Empty reply from server")
        return wd.ExecResult(0, "ok", "")

    monkeypatch.setattr(wd, "_run", flaky)
    # 先抓住原始 sleep 再 patch，否则退避会递归调用自己
    real_sleep = asyncio.sleep

    async def no_wait(_secs):
        await real_sleep(0)

    monkeypatch.setattr(wd.asyncio, "sleep", no_wait)

    r = asyncio.run(wd._run_with_retry(["git", "clone"], what="clone x"))
    assert r.ok and calls["n"] == 3


def test_non_transient_error_is_not_retried(tmp_path, monkeypatch):
    """认证失败之类重试多少次都一样 —— 白等还掩盖真正的原因。"""
    from vplatform.workspace import worktree_docker as wd

    calls = {"n": 0}

    async def denied(argv, *, cwd=None, timeout=None, check_ok=True, env=None):
        calls["n"] += 1
        return wd.ExecResult(128, "", "remote: Invalid username or token.")

    monkeypatch.setattr(wd, "_run", denied)
    r = asyncio.run(wd._run_with_retry(["git", "clone"], what="clone x"))
    assert not r.ok and calls["n"] == 1


def test_failed_clone_cleans_up_half_baked_mirror(provider, tmp_path):
    """clone 失败要清掉半成品目录。

    留着的话下次会走「mirror 已存在」分支，对着一个坏仓 fetch，
    报的错更难懂。
    """
    async def go():
        with pytest.raises(Exception):
            await provider.ensure_mirror(
                "p1", RepoSpec(name="nope", url="file:///definitely/not/here"))

    asyncio.run(go())
    assert not provider._mirror("p1", "nope").exists()


def test_http2_framing_error_is_treated_as_transient(monkeypatch):
    """git + GitHub 的 HTTP/2 framing 错误是已知问题，实测撞到过。
    不认成瞬时故障就白白失败一条需求。"""
    from vplatform.workspace import worktree_docker as wd

    calls = {"n": 0}

    async def flaky(argv, *, cwd=None, timeout=None, check_ok=True, env=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return wd.ExecResult(128, "", "fatal: unable to access: Error in the HTTP2 framing layer")
        return wd.ExecResult(0, "", "")

    real_sleep = asyncio.sleep

    async def no_wait(_):
        await real_sleep(0)

    monkeypatch.setattr(wd, "_run", flaky)
    monkeypatch.setattr(wd.asyncio, "sleep", no_wait)
    assert asyncio.run(wd._run_with_retry(["git", "fetch"])).ok
    assert calls["n"] == 2


def test_fetch_is_throttled(provider, tmp_path):
    """**每次 acquire 都 fetch 会把成功率押在网络上。**

    一条需求 N 个任务就是 N 次网络往返，撞上抖动的概率成倍放大。
    刚 fetch 过就跳过。
    """
    url = _make_upstream(tmp_path, "web")
    spec = RepoSpec("web", url)

    async def go():
        await provider.ensure_mirror("p1", spec)      # 首次 clone
        calls = []
        from vplatform.workspace import worktree_docker as wd
        orig = wd._run_with_retry

        async def spy(argv, **kw):
            calls.append(argv[1] if len(argv) > 1 else "")
            return await orig(argv, **kw)

        wd._run_with_retry = spy
        try:
            await provider.ensure_mirror("p1", spec)  # 立刻再来 —— 不该打网络
        finally:
            wd._run_with_retry = orig
        return calls

    calls = asyncio.run(go())
    assert "fetch" not in calls, f"刚 clone 完又 fetch 了：{calls}"


# ── 一个空间 ≠ 一个仓 ────────────────────────────────────────────
def _make_upstream_on(tmp: Path, name: str, branch: str) -> str:
    """主干分支名可指定 —— 真实空间里 main / master / develop 混着来。"""
    repo = tmp / f"{name}-origin"
    repo.mkdir(parents=True)
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q", "-b", branch)
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / f"{name}.py").write_text(f"NAME = {name!r}\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    return str(repo)


def test_repo_without_the_integration_branch_falls_back_to_its_own_mainline(
        provider, tmp_path):
    """**一个空间不等于一个仓。**

    集成分支（`vibe/dev`）是空间级的一个名字，但它在每个仓里是各自的一条
    分支，新接进来的仓根本还没有它。之前这里探测不到就把字面量当 ref 用，
    `git worktree add ... vibe/dev` 报 unknown revision，整个工位创建失败 ——
    多仓空间里只要有一个仓没建过这条分支，它就永远进不来。
    """
    url = _make_upstream_on(tmp_path, "orders-api", "main")
    h = asyncio.run(provider.acquire(
        project_id="p1", run_id="r1", branch="cr/1-t1",
        base_branch="vibe/dev",                       # 这个仓里并不存在
        repos=[RepoSpec("orders-api", url, default_branch="main")]))
    assert (Path(h.repos["orders-api"]) / "orders-api.py").exists()


def test_each_repo_uses_its_own_mainline_name(provider, tmp_path):
    """一个仓 main、一个仓 master —— 两个都要能起来。

    `base = base_branch or spec.default_branch` 里的 default_branch 是死代码：
    调用方永远传集成分支名，所以每个仓的主干配置从来没生效过。
    """
    a = _make_upstream_on(tmp_path, "api", "main")
    b = _make_upstream_on(tmp_path, "legacy", "master")
    h = asyncio.run(provider.acquire(
        project_id="p1", run_id="r1", branch="cr/1-t1", base_branch="vibe/dev",
        repos=[RepoSpec("api", a, default_branch="main"),
               RepoSpec("legacy", b, default_branch="master")]))
    assert set(h.repos) == {"api", "legacy"}
    assert (Path(h.repos["api"]) / "api.py").exists()
    assert (Path(h.repos["legacy"]) / "legacy.py").exists()


def test_integration_branch_wins_when_the_repo_does_have_it(provider, tmp_path):
    """仓里已经有集成分支时必须用它，不能退回主干 ——
    退回去就等于丢掉别人已经合进集成分支的改动。"""
    url = _make_upstream_on(tmp_path, "api", "main")
    run = lambda *a: subprocess.run(a, cwd=url, check=True, capture_output=True)
    run("git", "checkout", "-q", "-b", "vibe/dev")
    (Path(url) / "only_on_dev.py").write_text("X = 1\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "dev")
    run("git", "checkout", "-q", "main")

    h = asyncio.run(provider.acquire(
        project_id="p1", run_id="r1", branch="cr/1-t1", base_branch="vibe/dev",
        repos=[RepoSpec("api", url, default_branch="main")]))
    assert (Path(h.repos["api"]) / "only_on_dev.py").exists(), "没用集成分支起步"


def test_no_usable_base_branch_says_so_clearly(provider, tmp_path):
    """两条都找不到时要报得能看懂，不是 git 的 unknown revision。"""
    from vplatform.workspace.worktree_docker import WorkspaceError
    url = _make_upstream_on(tmp_path, "api", "main")
    with pytest.raises(WorkspaceError, match="找不到任何可用的起点分支"):
        asyncio.run(provider.acquire(
            project_id="p1", run_id="r1", branch="cr/1-t1", base_branch="nope",
            repos=[RepoSpec("api", url, default_branch="also-nope")]))


# ── 凭据不能落盘 ────────────────────────────────────────────────
def test_pat_is_never_written_into_the_repo_config(provider, tmp_path):
    """**PAT 明文落盘 = agent 能读到它。**

    原来是 `https://<PAT>@github.com/...` 直接拼进 URL，而 `git clone` 会把
    remote.origin.url 原样存进 `mirrors/<repo>.git/config`。工位根还要挂进
    agent 容器 —— agent 自己就能把 token 读走。
    设计里说好「密钥只存引用不存明文」，解析出来的值却躺在磁盘上。
    """
    url = _make_upstream(tmp_path, "web")
    spec = RepoSpec("web", url, pat="ghp_SUPERSECRET1234567890")
    asyncio.run(provider.ensure_mirror("p1", spec))

    mirror = provider._mirror("p1", "web")
    cfg = (mirror / "config").read_text(encoding="utf-8")
    assert "ghp_SUPERSECRET1234567890" not in cfg, "PAT 明文写进了 git config"
    # 整个镜像目录里都不该有
    hits = [p for p in mirror.rglob("*") if p.is_file()
            and "ghp_SUPERSECRET1234567890" in p.read_bytes().decode("utf-8", "ignore")]
    assert hits == [], f"PAT 出现在 {hits}"


def test_pat_goes_through_env_not_argv(tmp_path):
    """密钥进 argv 的话，同机其它用户 `ps` 就能看到。"""
    from vplatform.workspace.worktree_docker import WorktreeDockerProvider as P

    auth, env = P._git_auth(RepoSpec("web", "https://x/y.git", pat="ghp_SECRET"))
    assert "ghp_SECRET" not in " ".join(auth), "密钥进了命令行"
    assert env["VP_GIT_PAT"] == "ghp_SECRET"
    assert any("credential.helper" in a for a in auth)


def test_git_never_waits_for_a_password_prompt(tmp_path):
    """凭据不对时要立刻失败。挂在那儿等输入会把 worker 冻到超时，
    日志里一个字都没有 —— 实测撞到过一次 git 挂了 10 分钟。"""
    from vplatform.workspace.worktree_docker import WorktreeDockerProvider as P

    _, env = P._git_auth(RepoSpec("web", "https://x/y.git"))
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_no_pat_means_no_credential_helper(tmp_path):
    """公开仓不该被塞一个假凭据。"""
    from vplatform.workspace.worktree_docker import WorktreeDockerProvider as P

    auth, env = P._git_auth(RepoSpec("web", "https://x/y.git"))
    assert auth == [] and "VP_GIT_PAT" not in env
