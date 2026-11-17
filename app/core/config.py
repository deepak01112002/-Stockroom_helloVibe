from __future__ import annotations

from functools import lru_cache

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
