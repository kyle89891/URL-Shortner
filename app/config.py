from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "mysql+pymysql://root:password@localhost:3306/urlshortener"
    redis_url: str = "redis://localhost:6379/0"
    base_host: str = "http://localhost:8000"
    cache_ttl_seconds: int = 3600
    rate_limit_max_requests: int = 10
    rate_limit_window_seconds: int = 60
    click_flush_interval_seconds: int = 10


settings = Settings()
