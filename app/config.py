from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Panga Branch API"
    app_env: str = "dev"
    database_url: str = "postgresql+psycopg2://panga:panga@localhost:5432/panga"
    redis_url: str = "redis://localhost:6379/0"

    branch_bank_name: str = "Panga Branch Bank"
    branch_base_url: str = "http://localhost:8081"
    central_bank_base_url: str = "https://test.diarainfra.com/central-bank"
    fallback_bank_id: str = "LOC001"

    heartbeat_interval_seconds: int = 600
    directory_cache_ttl_seconds: int = 300
    pending_retry_max_seconds: int = 3600
    pending_timeout_seconds: int = 4 * 3600

    supported_currencies: str = "EUR,USD,GBP,SEK,LVL"

    jwt_private_key_path: str = "./data/private_key.pem"
    jwt_public_key_path: str = "./data/public_key.pem"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if settings.database_url.startswith("postgres://"):
        settings.database_url = settings.database_url.replace("postgres://", "postgresql+psycopg2://", 1)
    return settings
