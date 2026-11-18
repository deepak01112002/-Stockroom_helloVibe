from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and an optional local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Stockroom"
    database_url: str = Field(
        default="postgresql+psycopg://stockroom:stockroom@localhost:5432/stockroom",
        description="Safe local-development PostgreSQL connection URL.",
    )
    log_level: str = "INFO"
    supplier_base_url: str = "https://dummyjson.com"
    supplier_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    supplier_max_retries: int = Field(default=3, ge=0, le=5)
    supplier_page_size: int = Field(default=50, ge=1, le=100)
    hold_ttl_minutes: int = Field(default=15, ge=1, le=60)
    hold_rate_limit_per_minute: int = Field(default=30, ge=1, le=1_000)
    expiry_worker_batch_size: int = Field(default=100, ge=1, le=500)
    expiry_worker_interval_seconds: float = Field(default=5.0, gt=0, le=300)
    payment_webhook_secret: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
