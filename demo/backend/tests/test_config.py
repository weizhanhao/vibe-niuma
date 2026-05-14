from demo_backend.config import Settings


def test_settings_reads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@h:3306/db")
    settings = Settings()
    assert settings.database_url == "mysql+pymysql://u:p@h:3306/db"


def test_settings_has_default_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings()
    assert settings.database_url.startswith("mysql+pymysql://")
