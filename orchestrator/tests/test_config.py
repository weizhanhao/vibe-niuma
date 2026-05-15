from orchestrator.config import Settings


def test_settings_reads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@h:3307/db")
    monkeypatch.setenv("QUOTA_SIZE", "9")
    settings = Settings()
    assert settings.database_url == "mysql+pymysql://u:p@h:3307/db"
    assert settings.quota_size == 9


def test_settings_has_defaults(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("QUOTA_SIZE", raising=False)
    monkeypatch.delenv("IDLE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("REAPER_INTERVAL_SECONDS", raising=False)
    settings = Settings()
    assert settings.database_url.startswith("mysql+pymysql://")
    assert settings.quota_size == 5
    assert settings.idle_ttl_seconds == 1800
    assert settings.reaper_interval_seconds == 60


def test_adapter_settings_have_defaults(monkeypatch):
    for key in (
        "DEV_RUNNER", "DEV_MODEL", "ANTHROPIC_BASE_URL", "LLM_API_KEY",
        "VISION_MODEL", "PREVIEW_PORT_MIN", "PREVIEW_PORT_MAX",
        "DOCKER_NETWORK", "DEV_RUNNER_TIMEOUT_SECONDS", "PREVIEW_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    assert settings.dev_runner == "claude-code"
    assert settings.dev_model == "deepseek-chat"
    assert settings.anthropic_base_url == "http://localhost:8787"
    assert settings.llm_api_key == ""
    assert settings.vision_model == "qwen-vl-plus"
    assert settings.preview_port_min == 5100
    assert settings.preview_port_max == 5199
    assert settings.docker_network == "bridge"
    assert settings.dev_runner_timeout_seconds == 900
    assert settings.preview_host == "localhost"


def test_adapter_settings_read_env(monkeypatch):
    monkeypatch.setenv("DEV_RUNNER", "opencode")
    monkeypatch.setenv("DEV_MODEL", "qwen-max")
    monkeypatch.setenv("PREVIEW_PORT_MIN", "6000")
    monkeypatch.setenv("PREVIEW_HOST", "ecs.example.com")
    settings = Settings()
    assert settings.dev_runner == "opencode"
    assert settings.dev_model == "qwen-max"
    assert settings.preview_port_min == 6000
    assert settings.preview_host == "ecs.example.com"
