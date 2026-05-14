from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://root:demopass@localhost:3307/orchestrator"
    demo_repo_path: str = "/Users/weizhanhao/doskill/demo"
    quota_size: int = 5
    idle_ttl_seconds: int = 1800
    reaper_interval_seconds: int = 60


settings = Settings()
