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
