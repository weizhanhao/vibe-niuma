from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://root:demopass@localhost:3307/orchestrator"
    demo_repo_path: str = "/Users/weizhanhao/doskill/demo"
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

    # —— repo /init ——
    repo_init_timeout_seconds: int = 600        # 扫仓库 → 生成 CLAUDE.md
    repo_init_doc_filename: str = "AGENTS.md"   # 仓库根目录的项目知识文件（agents.md 跨厂商约定）


settings = Settings()
