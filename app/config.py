from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Auth
    api_keys_raw: str = Field(default="devkey1", alias="API_KEYS")

    # DB
    database_url: str = "sqlite+aiosqlite:///./reviews.db"

    # Observability
    log_level: str = "INFO"
    rate_limit_per_minute: int = 60
    image_max_bytes: int = 5_242_880

    # Fast pool: comma-separated "prefix:model_id" entries
    # Supported prefixes: nim, anthropic
    fast_pool: str = (
        "nim:meta/llama-3.3-70b-instruct,"
        "anthropic:claude-haiku-4-5"
    )

    # Rich pool: strong models for two-stage analysis
    rich_pool: str = (
        "nim:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning,"
        "anthropic:claude-opus-4-8"
    )

    # Provider credentials
    anthropic_api_key: SecretStr = SecretStr("")
    nim_api_key: SecretStr = SecretStr("")
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"

    # Retry config (applied per pool member)
    retry_max_attempts: int = 2
    retry_initial_wait_s: float = 1.0
    retry_max_wait_s: float = 10.0
    provider_timeout_s: float = 30.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def api_keys(self) -> list[str]:
        keys = [k.strip() for k in self.api_keys_raw.split(",") if k.strip()]
        return keys if keys else ["devkey1"]

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
