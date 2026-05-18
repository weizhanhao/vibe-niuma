"""orchestrator 全局配置 —— Plan 6 后 DB 主导，env / .env 作为兜底默认值。

Plan 6 之前：模块级 `settings = Settings()` 单例，启动时读 env 就锁定。
Plan 6 之后：
  - `get_settings()` 用 `lru_cache(maxsize=1)` 缓存；
  - `/admin/config` PUT 后调 `get_settings.cache_clear()` 让下次访问重读；
  - 兼容老代码 `from orchestrator.config import settings` + `settings.xxx`：
    用 `_SettingsProxy` 把属性访问透传到 `get_settings()`，
    `monkeypatch.setattr(main_mod.settings, "demo_repo_path", ...)` 依然有效。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://root:demopass@localhost:3307/orchestrator"
    demo_repo_path: str = "/Users/weizhanhao/vibe-niuma/demo"
    quota_size: int = 5
    idle_ttl_seconds: int = 1800
    reaper_interval_seconds: int = 60

    # —— Plan 3 / Plan 5 adapter & deploy settings ——
    dev_runner: str = "claude-code"             # "claude-code" | "opencode"
    dev_model: str = "deepseek-chat"
    anthropic_base_url: str = "http://localhost:8787"   # Anthropic-compatible 代理
    llm_api_key: str = ""                       # dev runner + 澄清共用，或可拆
    vision_model: str = "qwen-vl-plus"          # 看截图的视觉模型
    preview_port_min: int = 5100
    preview_port_max: int = 5199
    docker_network: str = "bridge"
    dev_runner_timeout_seconds: int = 900
    preview_host: str = "localhost"             # 拼预览 URL 用：ECS 上换公网地址
    # 预览容器只起前端，API 通过 vite proxy → backend；空串则不注入 VITE_API_URL，
    # 业务员看到的页面会是空数据。指向 main-demo 后端就能复用真实样板数据。
    preview_backend_url: str = ""
    # 合并成功后跑这个脚本（异步），重建 main-demo 容器加载新前端代码。
    # 空串则跳过 —— 本地开发或自定义部署可以关掉。
    main_demo_refresh_script: str = ""

    # —— repo /init ——
    repo_init_timeout_seconds: int = 600        # 扫仓库 → 生成 CLAUDE.md
    repo_init_doc_filename: str = "AGENTS.md"   # 仓库根目录的项目知识文件（agents.md 跨厂商约定）

    # —— Plan 11 多仓 sync ——
    # 业务员配的 N 个 GitHub 仓 clone 到这下面：<workspaces_root>/<project_id>/<repo_name>/
    # 默认放 /opt/vibe-niuma/workspaces；本地开发覆写到 tmp。
    workspaces_root: str = "/opt/vibe-niuma/workspaces"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例 Settings —— 第一次调时实例化，之后命中缓存。

    `/admin/config` PUT 改了 dev_runner / dev_model 等字段后会调
    `get_settings.cache_clear()`，下次 callers 再调就拿到新实例。
    """
    return Settings()


class _SettingsProxy:
    """老代码 `from orchestrator.config import settings; settings.dev_runner` 兼容层。

    每次属性读都走 `get_settings()` —— 缓存失效后自动拿到新值。
    `setattr` 打到当前缓存实例上：保持 monkeypatch.setattr(main_mod.settings, ...)
    在 conftest.py / 现有测试里继续工作。
    """

    def __getattr__(self, name: str):
        return getattr(get_settings(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(get_settings(), name, value)


settings = _SettingsProxy()
