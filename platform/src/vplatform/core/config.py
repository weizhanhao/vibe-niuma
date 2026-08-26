"""平台配置。

**密钥不进 DB**（§4.2）：Project.secret_refs 只存引用如 "env:DASHSCOPE_API_KEY"，
实际值由 resolve_secret() 从环境/密钥管理取。
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VP_", extra="ignore")

    database_url: str = "mysql+pymysql://root:demopass@127.0.0.1:3306/vplatform"

    # 事件总线（§13）：MySQL 持久化 + Redis 实时 fan-out
    redis_url: str = ""            # 空 = 单进程模式，只用进程内 fan-out
    event_buffer: int = 1000

    # worker 分级轮询（§7.5 ①）—— 别为了延迟把间隔压到 50ms，那是白烧 DB
    poll_interactive_ms: int = 200
    poll_background_ms: int = 2000
    poll_backoff_max_ms: int = 5000
    worker_concurrency: int = 4
    job_lock_timeout_s: int = 900     # 超时后其他 worker 可接管

    # Workspace（§5）
    workspaces_root: str = "/data/projects"
    workspace_image: str = "vplatform/workspace:base"
    port_lease_ttl_s: int = 1800

    # AgentSession（§6）
    opencode_bin: str = "opencode"
    # cli = 解析 stdout（拿不到 reasoning）；serve = HTTP + 全局事件流（逐 token）
    agent_runner: str = "serve"
    # opencode 要 `provider/model` 格式。Project.dev_model 存的是裸模型名，
    # 这里补前缀 —— 不补会得到 `Model not found: <model>/.`
    agent_provider: str = "dashscope"
    opencode_server_port_base: int = 4096
    agent_timeout_s: int = 900

    # Review（§9）—— DashScope 端点 + --no-filter + 自建过滤
    ocr_bin: str = "ocr"
    ocr_provider: str = "dashscope"
    ocr_model: str = "deepseek-v4-pro"
    ocr_concurrency: int = 4
    ocr_use_builtin_filter: bool = False   # §9.11：内置过滤 17 倍贵且不用

    # 自建过滤层（§9.10 第一层）
    filter_endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    filter_model: str = "deepseek-v4-pro"

    # Skill 层（§14.3 L1）
    platform_skills_dir: str = "/opt/vplatform/skills"

    mergiraf_bin: str = "mergiraf"

    # 浏览器自检（ego lite）。目前只有 macOS 版，且是宿主上的桌面应用 ——
    # 容器里的 agent 够不着，所以这一环只在宿主模式下真跑。
    ego_browser_bin: str = "ego-browser"

    # **opencode 的状态目录必须跟 ego lite 分开。**
    # 两者都用 `$XDG_DATA_HOME/opencode`（默认 `~/.local/share/opencode`），
    # 但它们是不同版本、不同 sqlite schema。实测装完 ego lite 之后，
    # opencode CLI 直接报 `no such column: replacement_seq` —— 平台的
    # agent 层整个跑不动，而错误信息完全看不出跟浏览器有关。
    # 空串 = 用默认路径（不隔离）。
    opencode_data_home: str = "~/.vplatform/opencode-data"

    # **干跑模式**：整条流水线照跑，但不往远端推。
    # 用于首次接入、演练、以及在别人的仓库上验证平台本身。
    # 跳过时会在合并阶梯里如实留痕，不会看起来像推成功了。
    push_enabled: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class SecretError(RuntimeError):
    """密钥解析失败。统一收口，不让底层 KeyError 泄漏。"""


def resolve_secret(ref: str | None) -> str:
    """把引用解析成真实密钥值。

    支持的形式：
        env:NAME     从环境变量取
        literal:xxx  字面量 —— **仅供测试**，生产禁用

    v1 把 API key 明文存进 system_config 表（String(256)）。这里不重复那个错误：
    DB 里只有引用，泄库不泄密钥。
    """
    if not ref:
        raise SecretError("密钥引用为空")
    scheme, _, rest = ref.partition(":")
    if scheme == "env":
        val = os.environ.get(rest)
        if not val:
            raise SecretError(f"环境变量 {rest} 未设置（引用 {ref!r}）")
        return val
    if scheme == "literal":
        return rest
    raise SecretError(f"不支持的密钥引用格式：{ref!r}（应为 env:NAME）")
