"""WorktreeDockerProvider —— git worktree 打底 + Docker 包一层（D2）。

磁盘布局：
    <root>/<project_id>/
      mirrors/<repo>.git        bare mirror —— object store 共享，只 fetch 一次
      workspaces/<run_id>/
        <repo_a>/               git worktree add，秒级
        <repo_b>/

为什么不是 v1 那样单工作树 checkout 切分支：
`git_manager.create_branch` 会 stash + reset --hard + clean，两个并发 Run
第二个会直接抹掉第一个 agent 正在写的文件。worktree 各有各的工作目录，
共享 .git object store，创建是秒级的。

三个坑（§5.3）在这里落地：
  坑 1 依赖安装 → 项目级预烘焙镜像 + 包管理器 store 只读挂载共享
  坑 2 端口     → ports.PortLeaseManager（DB 唯一索引）
  坑 3 多仓原子 → acquire/release 对 N 个仓统一处理，任一失败整体回滚
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from vplatform.workspace.provider import (
    ExecResult,
    RepoSpec,
    WorkspaceError,
    WorkspaceHandle,
)

logger = logging.getLogger(__name__)

# 锁文件 → 预烘焙镜像的 key。改了锁文件才重建镜像（坑 1）
LOCKFILES = (
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "uv.lock", "poetry.lock", "requirements.txt", "go.sum", "Cargo.lock",
)


async def _run(argv: list[str], *, cwd: str | Path | None = None,
               timeout: float | None = 300, check_ok: bool = True,
               env: dict[str, str] | None = None) -> ExecResult:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd) if cwd else None,
        env={**os.environ, **(env or {})} if env else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise WorkspaceError(f"命令超时（{timeout}s）: {' '.join(argv[:3])}…") from exc
    return ExecResult(proc.returncode or 0,
                      out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


# 网络瞬时故障的特征。实测遇到过 "Empty reply from server"，
# 以及一个 CPU 时间只有 0.02 秒却挂了 13 分钟的 `git clone`。
_TRANSIENT = ("empty reply from server", "could not resolve host",
              "connection reset", "connection timed out", "timed out",
              "ssl_error", "rpc failed", "early eof", "the remote end hung up",
              # git + GitHub 的已知问题，实测撞到过。缓解手段见
              # ensure_mirror 里的 http.version=HTTP/1.1
              "http2 framing layer", "http/2 stream", "gnutls_handshake",
              "operation too slow", "transfer closed")


async def _run_with_retry(argv: list[str], *, cwd=None, timeout: float = 600,
                          attempts: int = 3, what: str = "",
                          env: dict[str, str] | None = None) -> ExecResult:
    """网络类命令要重试。

    单次超时也要收紧：clone 之前配的是 1800 秒，一个卡死的 clone 能占住
    worker 半小时（实测遇到过），而这类卡死重试一次往往就好了。
    """
    last: ExecResult | None = None
    for i in range(attempts):
        try:
            r = await _run(argv, cwd=cwd, timeout=timeout, env=env)
        except WorkspaceError as exc:
            if i == attempts - 1:
                raise
            logger.warning("%s 第 %d 次超时，重试：%s", what or argv[0], i + 1, exc)
            await asyncio.sleep(2 ** i)
            continue
        if r.ok:
            return r
        last = r
        err = (r.stderr or "").lower()
        if not any(t in err for t in _TRANSIENT) or i == attempts - 1:
            return r
        logger.warning("%s 第 %d 次遇到瞬时网络错误，重试：%s",
                       what or argv[0], i + 1, r.stderr.strip()[:120])
        await asyncio.sleep(2 ** i)
    return last or ExecResult(1, "", "重试耗尽")


class DockerRunner:
    """把 docker 调用收在一处，方便测试替身与将来换 K8s。"""

    def __init__(self, binary: str = "docker"):
        self.bin = binary

    async def image_exists(self, tag: str) -> bool:
        r = await _run([self.bin, "image", "inspect", tag], timeout=60)
        return r.ok

    async def build(self, tag: str, context: Path, dockerfile: str) -> ExecResult:
        df = context / "Dockerfile.vplatform"
        df.write_text(dockerfile, encoding="utf-8")
        try:
            return await _run([self.bin, "build", "-t", tag, "-f", str(df), str(context)],
                              timeout=1800)
        finally:
            df.unlink(missing_ok=True)

    async def start(self, *, name: str, image: str, mounts: list[tuple[str, str]],
                    port: int | None, network: str | None,
                    workdir: str | None = None) -> str:
        argv = [self.bin, "run", "-d", "--name", name]
        for host, guest in mounts:
            argv += ["-v", f"{host}:{guest}"]
        if workdir:
            argv += ["-w", workdir]
        if port:
            argv += ["-p", f"{port}:{port}", "-e", f"PORT={port}"]
        if network:
            argv += ["--network", network]
        argv += [image, "sleep", "infinity"]
        r = await _run(argv, timeout=300)
        if not r.ok:
            raise WorkspaceError(f"容器启动失败: {r.stderr.strip()[:400]}")
        return r.stdout.strip()

    async def stop(self, container_id: str) -> None:
        await _run([self.bin, "rm", "-f", container_id], timeout=120)

    async def exec(self, container_id: str, argv: list[str], *, workdir: str | None,
                   timeout: float | None) -> ExecResult:
        cmd = [self.bin, "exec"]
        if workdir:
            cmd += ["-w", workdir]
        cmd += [container_id, *argv]
        return await _run(cmd, timeout=timeout)


class WorktreeDockerProvider:
    """实现 WorkspaceProvider Protocol。"""

    def __init__(self, *, root: str | Path, base_image: str = "vplatform/workspace:base",
                 docker: DockerRunner | None = None, network: str | None = None,
                 shared_stores: list[tuple[str, str]] | None = None,
                 use_container: bool = True):
        self.root = Path(root)
        self.base_image = base_image
        self.docker = docker or DockerRunner()
        self.network = network
        # 包管理器 store 只读共享（坑 1）：装过的包不必每个工位再装一遍
        self.shared_stores = shared_stores or []
        self.use_container = use_container

    # ── 路径 ────────────────────────────────────────────────────
    def _mirror(self, project_id: str, repo: str) -> Path:
        return self.root / project_id / "mirrors" / f"{repo}.git"

    def _ws_root(self, project_id: str, run_id: str) -> Path:
        return self.root / project_id / "workspaces" / run_id

    # ── mirror 维护 ─────────────────────────────────────────────
    async def _resolve_base(self, mirror, spec, base_branch: str | None) -> str:
        """这个仓该从哪个 ref 起步。

        **一个空间不等于一个仓。** 集成分支（`Project.target_branch`，
        比如 `vibe/dev`）是空间级的一个名字，但它在每个仓里是**各自的一条分支**，
        而且很多仓根本还没有它 —— 新接进来的仓只有自己的主干，
        主干还可能是 `main` / `master` / `develop` 各不相同。

        之前这里写死拿 `origin/<集成分支>`，探测不到就把字面量当 ref 用，
        于是 `git worktree add ... vibe/dev` 报 unknown revision，
        整个工位创建失败。单仓空间你手动建一次分支就绕过去了；
        多仓空间里只要有一个仓没建，这个仓就永远进不来。

        顺序：集成分支（远端 → 本地）→ 这个仓自己的主干（远端 → 本地）。
        落到主干上是**正常路径**，不是降级 —— 仓第一次参与就该从主干起步。
        """
        wanted = [b for b in (base_branch, spec.default_branch) if b]
        for b in wanted:
            probe = await _run(["git", "rev-parse", "--verify",
                                f"refs/remotes/origin/{b}"], cwd=mirror,
                               check_ok=False, timeout=60)
            if probe.ok:
                return f"refs/remotes/origin/{b}"
        for b in wanted:
            probe = await _run(["git", "rev-parse", "--verify", b], cwd=mirror,
                               check_ok=False, timeout=60)
            if probe.ok:
                return b
        raise WorkspaceError(
            f"仓 {spec.name} 里找不到任何可用的起点分支："
            f"试过 {wanted}（集成分支 + 仓主干）。"
            f"请确认 default_branch 配置对不对。")

    @staticmethod
    def _git_auth(spec: RepoSpec) -> tuple[list[str], dict[str, str]]:
        """把凭据交给 git，但**不写进 URL、不写进 argv**。

        原来是 `https://<PAT>@github.com/...` 直接拼进 URL —— 而 git clone
        会把 remote.origin.url **原样存进 `mirrors/<repo>.git/config`**，
        PAT 就此明文落盘。更糟的是工位根要挂进 agent 容器，
        **agent 自己就能读到这个 token**。设计里说好「密钥只存引用不存明文」，
        解析出来的值却躺在磁盘上。

        改成：URL 保持干净，凭据经环境变量交给一个 credential helper。
        helper 脚本本身进 argv（无害），密钥只在子进程 env 里 ——
        既不落盘，也不会在 `ps` 里被同机其它用户看到。

        顺带 `GIT_TERMINAL_PROMPT=0`：凭据不对时立刻失败，
        而不是挂在那儿等人输密码 —— 那会把 worker 冻到超时，
        日志里一个字都没有。
        """
        env = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GCM_INTERACTIVE": "never"}
        if not spec.pat:
            return [], env
        env["VP_GIT_PAT"] = spec.pat
        helper = ("!f(){ echo username=x-access-token; "
                  "echo password=\"$VP_GIT_PAT\"; }; f")
        return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"], env

    async def ensure_mirror(self, project_id: str, spec: RepoSpec) -> Path:
        """bare mirror 是 object store —— 每个仓只 clone 一次，之后只 fetch。

        worktree 全部挂在它上面，所以创建工位不需要重新传对象。
        """
        mirror = self._mirror(project_id, spec.name)
        url = spec.url                      # **保持干净** —— 凭据走 _git_auth
        auth, genv = self._git_auth(spec)

        # 刚 fetch 过就不再打网络。
        # 每次 acquire 都 fetch 一遍既慢又把成功率押在网络上 ——
        # 一条需求 N 个任务就是 N 次网络往返，撞上抖动的概率成倍放大。
        if mirror.exists() and self._recently_fetched(mirror):
            return mirror

        if mirror.exists():
            # **只把远端引用取到独立命名空间 refs/remotes/origin/**，
            # 绝不碰本地 refs/heads/。
            #
            # 原来用的是 `--prune '+refs/*:refs/*'` —— 那会把 origin 上不存在的
            # 本地分支删掉，而 `cr/<id>-t<n>` 正是这种分支（还没 push）。
            # 后果：另一个 Run 一 acquire 就触发 fetch，把前一个 Run 正在用的
            # 分支 prune 掉，agent 已提交的代码变成不可达对象直接蒸发。
            # git 不保护被活跃 worktree 检出的分支，实测确认。
            r = await _run_with_retry(
                ["git", *auth, "fetch", "--prune", "origin",
                 "+refs/heads/*:refs/remotes/origin/*",
                 "+refs/tags/*:refs/tags/*"],
                cwd=mirror, timeout=300, what=f"fetch {spec.name}", env=genv)
            if not r.ok:
                raise WorkspaceError(f"fetch {spec.name} 失败: {r.stderr.strip()[:400]}")
            self._mark_fetched(mirror)
            return mirror

        mirror.parent.mkdir(parents=True, exist_ok=True)
        r = await _run_with_retry(["git", *auth, "clone", "--mirror", url, str(mirror)],
                                  timeout=600, what=f"clone {spec.name}", env=genv)
        if not r.ok:
            # 失败要把半成品目录清掉，否则下次会走「mirror 已存在」分支
            # 对着一个坏仓 fetch，报的错更难懂
            shutil.rmtree(mirror, ignore_errors=True)
            raise WorkspaceError(f"clone {spec.name} 失败: {r.stderr.strip()[:400]}")
        # `clone --mirror` 会把 fetch refspec 设成 `+refs/*:refs/*`（mirror 语义）。
        # 留着它，后续任何 fetch 都会重蹈 C1 —— 显式改成隔离的 remotes 命名空间。
        await _run(["git", "config", "remote.origin.fetch",
                    "+refs/heads/*:refs/remotes/origin/*"], cwd=mirror, timeout=60)
        await _run(["git", "config", "remote.origin.mirror", "false"],
                   cwd=mirror, timeout=60)
        # 强制 HTTP/1.1。GitHub 上 HTTP/2 的 framing layer 错误是已知问题，
        # 实测撞到过；降到 1.1 基本消除。代价可忽略。
        await _run(["git", "config", "http.version", "HTTP/1.1"],
                   cwd=mirror, timeout=60)
        self._mark_fetched(mirror)
        return mirror

    def _recently_fetched(self, mirror: Path, *, within_s: int = 120) -> bool:
        stamp = mirror / "vp-last-fetch"
        try:
            return (time.time() - stamp.stat().st_mtime) < within_s
        except OSError:
            return False

    def _mark_fetched(self, mirror: Path) -> None:
        try:
            (mirror / "vp-last-fetch").touch()
        except OSError:
            pass

    # ── 依赖预烘焙（坑 1）───────────────────────────────────────
    def deps_fingerprint(self, worktrees: dict[str, str]) -> str:
        """锁文件内容的指纹。变了才重建镜像。

        用内容而不是 mtime —— worktree 每次新建 mtime 都变，用 mtime 等于永远重建。
        """
        h = hashlib.sha256()
        for name in sorted(worktrees):
            base = Path(worktrees[name])
            for lf in LOCKFILES:
                for p in sorted(base.rglob(lf)):
                    if "node_modules" in p.parts or ".git" in p.parts:
                        continue
                    h.update(f"{name}/{p.relative_to(base)}".encode())
                    h.update(p.read_bytes())
        return h.hexdigest()[:16]

    async def ensure_deps_image(self, project_id: str, worktrees: dict[str, str]) -> str:
        """项目级预烘焙镜像。命中就直接用，装依赖那 3 分钟省掉。

        **这一条不做，"并行"只是名义上的** —— worktree 秒级创建，但每个工位
        跑一次 npm i 就把优势全吃掉了。
        """
        fp = self.deps_fingerprint(worktrees)
        tag = f"vplatform/deps:{project_id[:12]}-{fp}"
        if await self.docker.image_exists(tag):
            return tag

        logger.info("预烘焙镜像 %s 不存在，构建中（锁文件指纹 %s）", tag, fp)
        lines = [f"FROM {self.base_image}", "WORKDIR /w"]
        installed_any = False
        for name, path in sorted(worktrees.items()):
            p = Path(path)
            if (p / "package.json").exists():
                lines += [
                    f"COPY {name}/package*.json {name}/pnpm-lock.yaml* /w/{name}/",
                    # **不加 `|| true`。**
                    # 之前尾部的 `|| true` 让安装失败也算构建成功 ——
                    # 产出一个「看起来烘焙了实际什么都没装」的镜像，
                    # 到 verify 环节才报 "No module named pytest"。
                    # 这正是我在 Dockerfile.workspace 里批评过的反模式。
                    f"RUN cd /w/{name} && (pnpm i --frozen-lockfile || npm ci || npm i)",
                ]
                installed_any = True
            if (p / "pyproject.toml").exists() or (p / "requirements.txt").exists():
                lines += [
                    f"COPY {name}/pyproject.toml* {name}/uv.lock* "
                    f"{name}/requirements.txt* /w/{name}/",
                    f"RUN cd /w/{name} && (uv sync --frozen || "
                    f"pip install --break-system-packages -r requirements.txt)",
                ]
                installed_any = True
            # 有 pytest.ini / tox.ini 却没在依赖里声明 pytest 的项目很常见
            # （doBuyRight 就是：104 个测试文件、有 pytest.ini、requirements 里没 pytest）。
            # verify 环节要跑测试，这里补上，否则报 "No module named pytest"，
            # 看起来像平台的问题，其实是仓库没声明。
            if (p / "pytest.ini").exists() or (p / "tox.ini").exists():
                lines.append("RUN pip install --break-system-packages pytest")
                installed_any = True

        if not installed_any:
            logger.info("没有可预烘焙的依赖声明，直接用 base 镜像")
            return self.base_image

        ctx = Path(next(iter(worktrees.values()))).parent
        r = await self.docker.build(tag, ctx, "\n".join(lines) + "\n")
        if not r.ok:
            # 回落到 base 是可以的（工位自己装，慢），但**必须吼出来**，
            # 否则没人知道预烘焙一直在失败，只会觉得"怎么这么慢"。
            logger.error("预烘焙失败，回落 base 镜像（每个工位都要自己装依赖，很慢）：\n%s",
                         (r.stderr or r.stdout).strip()[-800:])
            return self.base_image
        return tag

    # ── acquire / release（坑 3：多仓原子）──────────────────────
    async def acquire(self, *, project_id: str, run_id: str, branch: str,
                      base_branch: str, repos: list[RepoSpec],
                      port: int | None = None) -> WorkspaceHandle:
        """为 N 个仓各起一个 worktree + 一个容器。

        **任一步失败就整体回滚** —— 半个工位比没有工位更糟：
        后续步骤会以为环境就绪，在缺仓的目录里瞎改。
        """
        ws_id = uuid.uuid4().hex
        ws_root = self._ws_root(project_id, run_id)
        handle = WorkspaceHandle(id=ws_id, run_id=run_id, project_id=project_id,
                                 root=ws_root, branch=branch, port=port)
        try:
            ws_root.mkdir(parents=True, exist_ok=True)
            for spec in repos:
                mirror = await self.ensure_mirror(project_id, spec)
                dest = ws_root / spec.name
                base_ref = await self._resolve_base(mirror, spec, base_branch)

                r = await _run(["git", "worktree", "add", "-b", branch, str(dest),
                                base_ref], cwd=mirror, timeout=600)
                if not r.ok:
                    # 分支已存在。**必须先确认它是我们自己上一次留下的**，
                    # 不能盲目挂上去：origin 上可能有同名的历史分支被 fetch 进来，
                    # 那样 fallback 会静默检出几周前的代码，base_branch 被完全忽略。
                    r2 = await _run(["git", "worktree", "add", "--force",
                                     str(dest), branch], cwd=mirror, timeout=600)
                    if not r2.ok:
                        raise WorkspaceError(
                            f"worktree add {spec.name} 失败: {r.stderr.strip()[:300]}")
                    # 挂上了旧分支 → 强制对齐到 base，保证从最新代码起步
                    reset = await _run(["git", "reset", "--hard", base_ref],
                                       cwd=dest, check_ok=False, timeout=120)
                    if not reset.ok:
                        logger.warning("%s 的分支 %s 已存在且无法对齐到 %s",
                                       spec.name, branch, base_ref)
                handle.repos[spec.name] = str(dest)

            if not handle.repos:
                # 「半个工位比没有工位更糟」的极端情况：一个仓都没有。
                # 之前这里会走到 ensure_deps_image 的 next(iter(...)) 抛
                # StopIteration，报错信息完全无法解读。
                raise WorkspaceError(
                    f"没有可用的仓：请求了 {[s.name for s in repos]}，"
                    f"但空间 {project_id} 里一个都没注册")

            if self.use_container:
                image = await self.ensure_deps_image(project_id, handle.repos)
                # **宿主与容器内路径必须一致。**
                #
                # git worktree 的 `.git` 文件里写的是 bare mirror 的**绝对路径**
                # （`gitdir: /host/path/mirrors/x.git/worktrees/<id>`），
                # mirror 那边的 `gitdir` 也反指回 worktree 的绝对路径。
                # 只把 ws_root 挂到 /w 的话，容器里那两个路径都不存在，
                # `git` 直接报 "not a git repository" —— agent 没法 commit、
                # 拿不到 sha、冲突阶梯全废。实测确认。
                #
                # 挂整个 project 目录（含 mirrors 与 workspaces）到**同名路径**，
                # 两边路径一致，git 就正常了。这也正是 compose 里
                # 用 bind mount 而不是 named volume 的原因（DinD 由宿主
                # daemon 解析 -v，传容器内路径会挂到空目录）。
                project_root = self.root / project_id
                mounts = [(str(project_root), str(project_root)),
                          *self.shared_stores]
                handle.image = image
                handle.container_id = await self.docker.start(
                    name=f"vp-{run_id[:12]}-{ws_id[:6]}", image=image,
                    mounts=mounts, port=port, network=self.network,
                    workdir=str(ws_root),
                )
            return handle
        except Exception:
            await self.release(handle, best_effort=True)
            raise

    async def release(self, ws: WorkspaceHandle, *, best_effort: bool = False) -> None:
        """容器停 + worktree 摘除 + 目录删。失败不抛（除非明确要求）。"""
        errors: list[str] = []
        if ws.container_id:
            try:
                await self.docker.stop(ws.container_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"停容器: {exc}")
            ws.container_id = None

        for name, path in list(ws.repos.items()):
            mirror = self._mirror(ws.project_id, name)
            if mirror.exists():
                # --force：工作区可能有未提交改动，我们要的是释放工位不是保留它
                await _run(["git", "worktree", "remove", "--force", path],
                           cwd=mirror, timeout=120)
                await _run(["git", "worktree", "prune"], cwd=mirror, timeout=60)
            ws.repos.pop(name, None)

        if ws.root.exists():
            shutil.rmtree(ws.root, ignore_errors=True)

        if errors and not best_effort:
            raise WorkspaceError("；".join(errors))

    async def exec(self, ws: WorkspaceHandle, argv: list[str], *,
                   cwd: str | None = None, timeout: float | None = 900) -> ExecResult:
        """在工位里执行。有容器走 docker exec，没有就在宿主 worktree 里跑。"""
        if ws.container_id:
            # 路径与宿主一致（见 acquire 里的说明），所以直接用真实路径
            workdir = str(ws.root / cwd) if cwd else str(ws.root)
            return await self.docker.exec(ws.container_id, argv, workdir=workdir,
                                          timeout=timeout)
        target = ws.root / cwd if cwd else ws.root
        return await _run(argv, cwd=target, timeout=timeout)
