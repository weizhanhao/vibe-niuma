"""装配根（composition root）—— 把各层实现插到编排层上。

**这个文件之前不存在，是全平台最致命的缺口。**

后果：`handlers._caps` 永远是空 `Capabilities()`，所有环节走「缺少能力」分支
直接放行，需求一路"成功"穿过 12 个环节标成 done，而仓库里一行代码都没变。
110 个测试全绿，因为它们测的正是这条降级路径。

这里是唯一允许 import 具体实现的地方（接缝守卫按目录放行，见 check_seams.py
的 EXEMPT_FILES —— bootstrap 不在受管层里）。

按 project 装配：每个空间有自己的仓、模型、密钥，所以能力必须 per-project 解析，
不能是全局单例。`CapabilityFactory` 负责按 project_id 缓存与复用。
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from vplatform.core.config import Settings, get_settings, resolve_secret
from vplatform.core.models import Project
from vplatform.orchestration.handlers import Capabilities, configure
from vplatform.orchestration.dag import Pipeline, default_pipeline, load_pipeline

logger = logging.getLogger(__name__)


_SERVER = None
_SERVER_LOCK = threading.Lock()


def _shared_server(binary: str, env: dict, cwd: str = ""):
    """整个进程共用一个 server 池。

    池按**工位目录**分——serve 必须在那个目录里起，否则那一轮不执行
    （见 ServerPool 的说明）。同一工位复用同一个 server。
    """
    global _SERVER
    from vplatform.agents.opencode_server import ServerPool

    # **加锁。** 两个协程同时进来会各建一个池 → 同一个工位起两个 server，
    # 而事件是各自进程内的：prompt 发给了一个，订阅挂在另一个上，
    # 于是一条事件都收不到，任务跑到超时。
    with _SERVER_LOCK:
        if _SERVER is None:
            _SERVER = ServerPool(binary=binary, env=dict(env))
        else:
            _SERVER.env.update(env)      # 不同空间的 key 都要能用
        return _SERVER


class CapabilityFactory:
    """按 project 造 Capabilities。

    为什么不是全局单例：不同空间有不同的仓、不同的模型、不同的密钥引用。
    一个全局 `_caps` 只能服务一个空间 —— 那就不叫多租户平台了。
    """

    def __init__(self, settings: Settings | None = None):
        self.st = settings or get_settings()
        self._cache: dict[str, Capabilities] = {}

    # ── 各层实现 ────────────────────────────────────────────────
    def _workspace(self, project: Project):
        from vplatform.workspace.worktree_docker import WorktreeDockerProvider

        shared: list[tuple[str, str]] = []
        for host, guest in (("/pnpm-store", "/pnpm-store"), ("/uv-cache", "/uv-cache")):
            if Path(host).is_dir():
                shared.append((host, guest))
        return WorktreeDockerProvider(
            root=project.workspaces_root or self.st.workspaces_root,
            base_image=self.st.workspace_image,
            shared_stores=shared,
            # 宿主没有 docker 时退化为纯 worktree 隔离。**如实降级**：
            # 容器是第二层隔离，没有它 worktree 隔离仍然成立。
            use_container=_docker_available(),
        )

    def _agent(self, project: Project):
        from vplatform.agents.opencode import CliSession

        key = _secret(project, "llm")
        env = {"DASHSCOPE_API_KEY": key, "OPENAI_API_KEY": key} if key else {}
        # 把 opencode 的状态目录跟 ego lite 隔开 —— 见 Settings 里的说明。
        # 不隔开的话装了浏览器之后 agent 层直接跑不动。
        data_home = (self.st.opencode_data_home or "").strip()
        if data_home:
            root = Path(data_home).expanduser()
            root.mkdir(parents=True, exist_ok=True)
            env["XDG_DATA_HOME"] = str(root)
        model = (project.dev_model if "/" in (project.dev_model or "")
                 else f"{self.st.agent_provider}/{project.dev_model}")
        if (self.st.agent_runner or "serve").lower() == "serve":
            # **serve 才有 reasoning。** CLI 的 --format json 只吐
            # step/tool/text，模型在想什么一个字都拿不到。
            from vplatform.agents.opencode_server import ServerSession

            # 目录不存在就不传 —— 建目录留给真要用的时候（ensure），
            # 装配阶段不该因为一个还没建的路径就炸掉
            pool = _shared_server(self.st.opencode_bin, env)
            return ServerSession(pool, model=model,
                                 timeout=self.st.agent_timeout_s)
        return CliSession(
            binary=self.st.opencode_bin,
            model=project.dev_model,
            provider=self.st.agent_provider,
            env=env,
            timeout=self.st.agent_timeout_s,
        )

    def _reviewer(self, project: Project):
        from vplatform.review.ocr import OcrReviewAdapter

        key = _secret(project, "llm")
        if not key:
            logger.warning("空间 %s 没有可用的 llm 密钥，复核环节将被跳过", project.slug)
            return None
        return OcrReviewAdapter(
            binary=self.st.ocr_bin,
            concurrency=self.st.ocr_concurrency,
            use_builtin_filter=self.st.ocr_use_builtin_filter,
            env={"DASHSCOPE_API_KEY": key},
        )

    def _filter(self, project: Project):
        from vplatform.review.filter import FindingFilter

        key = _secret(project, "llm")
        if not key:
            return None
        return FindingFilter(endpoint=self.st.filter_endpoint, api_key=key,
                             model=self.st.filter_model)

    def _deployer(self, project: Project):
        from vplatform.deploy.selfhosted import SelfHostedDeploy

        # env_config 从 Project.config['deploy'] 读 —— 之前这一步没人做，
        # 于是 SelfHostedDeploy 永远拿不到命令、必然抛 DeployError。
        cfg = (project.config or {}).get("deploy") or {}
        if not cfg:
            return None
        return SelfHostedDeploy(env_config=cfg)

    def _host(self, project: Project):
        """GitHostAdapter。没有它 agent 的改动推不出工位。"""
        from vplatform.hosts.github import GitHubHost
        return GitHubHost()

    def _pipeline(self, project: Project) -> Pipeline:
        """流水线来自 Project.config['pipeline']（D8 的 "YAML in DB"）。

        之前所有空间共用 dag.py 里的模块级常量 —— 改流程要改源码重新发版，
        与「加环节只改 YAML」的验收标准不符。
        """
        raw = (project.config or {}).get("pipeline")
        if not raw:
            return default_pipeline()
        try:
            return load_pipeline(raw)
        except Exception:  # noqa: BLE001 —— 配错了不能让整个空间瘫痪
            logger.exception("空间 %s 的 pipeline 配置非法，回落到默认流水线", project.slug)
            return default_pipeline()

    # ── 装配 ────────────────────────────────────────────────────
    def build(self, project: Project) -> Capabilities:
        caps = Capabilities(
            workspace=self._workspace(project),
            agent=self._agent(project),
            reviewer=self._reviewer(project),
            finding_filter=self._filter(project),
            deployer=self._deployer(project),
            host=self._host(project),
            pipeline=self._pipeline(project),
        )
        self._cache[project.id] = caps
        return caps

    def for_project(self, project: Project) -> Capabilities:
        cached = self._cache.get(project.id)
        return cached if cached is not None else self.build(project)

    def invalidate(self, project_id: str) -> None:
        """空间配置改了就丢缓存 —— 否则改完密钥/模型要重启进程才生效。"""
        self._cache.pop(project_id, None)


_factory: CapabilityFactory | None = None


def get_factory() -> CapabilityFactory:
    global _factory
    if _factory is None:
        _factory = CapabilityFactory()
    return _factory


def ensure_host_skills(settings: Settings | None = None) -> int:
    """非容器模式下把 L1 平台级 skill 装进 agent 的 HOME。

    容器模式下这些 skill 烘焙在镜像里（§14.3 L1）。但
    `VP_DISABLE_CONTAINERS=1` 或宿主没 docker 时 agent 直接在宿主跑，
    `~/.config/opencode/skills/` 是空的 —— prompt 让它
    `Call the Skill tool with "to-tickets"`，工具根本不存在。

    返回装了几个。dist 不存在（没跑 platform-skills/build.sh）时返回 0
    并告警，不静默 —— 少了 skill 的环节会退化成"agent 自己瞎编一套做法"。
    """
    from vplatform.skills.installer import install_platform_skills

    st = settings or get_settings()
    dist = Path(st.platform_skills_dir)
    if not dist.is_dir():
        # 开发环境下 dist 通常在仓库里
        local = Path(__file__).resolve().parents[3] / "platform-skills" / "dist"
        if local.is_dir():
            dist = local
    if not dist.is_dir():
        logger.warning("找不到 skill dist（%s）—— 环节会缺 skill，"
                       "跑 platform-skills/build.sh 生成", st.platform_skills_dir)
        return 0
    home = Path(os.environ.get("HOME", "~")).expanduser()
    installed = install_platform_skills(dist, home)
    logger.info("已把 %d 个 L1 skill 装进 %s", len(installed), home / ".config/opencode/skills")
    return len(installed)


def install(settings: Settings | None = None) -> CapabilityFactory:
    """进程启动时调一次。worker_main 和 API lifespan 都要调。

    同时把 factory 交给 handlers —— 编排层按 job 的 project_id 现取能力，
    不再依赖那个全局空壳。
    """
    global _factory
    _factory = CapabilityFactory(settings)
    configure(_factory)
    if not _docker_available():
        # 宿主模式：skill 不在镜像里，得装到 HOME
        ensure_host_skills(settings)
    logger.info("装配完成：workspace=%s agent=%s ocr=%s",
                _docker_available() and "worktree+docker" or "worktree",
                get_settings().opencode_bin, get_settings().ocr_bin)
    return _factory


def _secret(project: Project, name: str) -> str:
    ref = (project.secret_refs or {}).get(name)
    if not ref:
        return ""
    try:
        return resolve_secret(ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning("空间 %s 的密钥 %s 解析失败：%s", project.slug, name, exc)
        return ""


def _docker_available() -> bool:
    from shutil import which
    return bool(which("docker")) and os.environ.get("VP_DISABLE_CONTAINERS") != "1"
