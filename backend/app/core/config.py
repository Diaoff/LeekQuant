from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Leek Quant"
    environment: str = Field(default="local", alias="ENVIRONMENT")
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    backend_cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="BACKEND_CORS_ORIGINS",
    )
    data_proxy_url: str | None = Field(default=None, alias="DATA_PROXY_URL")
    full_kline_sync_concurrency: int = Field(default=2, alias="FULL_KLINE_SYNC_CONCURRENCY")
    strategy_exec_timeout_seconds: float = Field(default=2.0, alias="STRATEGY_EXEC_TIMEOUT_SECONDS")
    strategy_exec_memory_mb: int = Field(default=256, alias="STRATEGY_EXEC_MEMORY_MB")
    strategy_exec_traceback_chars: int = Field(default=4000, alias="STRATEGY_EXEC_TRACEBACK_CHARS")

    @field_validator("database_url")
    @classmethod
    def require_asyncpg_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg://")
        return value

    @field_validator("full_kline_sync_concurrency")
    @classmethod
    def validate_full_kline_sync_concurrency(cls, value: int) -> int:
        if not 1 <= value <= 8:
            raise ValueError("FULL_KLINE_SYNC_CONCURRENCY must be between 1 and 8")
        return value

    @field_validator("strategy_exec_timeout_seconds")
    @classmethod
    def validate_strategy_exec_timeout_seconds(cls, value: float) -> float:
        if not 0.1 <= value <= 30:
            raise ValueError("STRATEGY_EXEC_TIMEOUT_SECONDS must be between 0.1 and 30")
        return value

    @field_validator("strategy_exec_memory_mb")
    @classmethod
    def validate_strategy_exec_memory_mb(cls, value: int) -> int:
        if not 64 <= value <= 2048:
            raise ValueError("STRATEGY_EXEC_MEMORY_MB must be between 64 and 2048")
        return value

    @field_validator("strategy_exec_traceback_chars")
    @classmethod
    def validate_strategy_exec_traceback_chars(cls, value: int) -> int:
        if not 500 <= value <= 20000:
            raise ValueError("STRATEGY_EXEC_TRACEBACK_CHARS must be between 500 and 20000")
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.backend_cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
