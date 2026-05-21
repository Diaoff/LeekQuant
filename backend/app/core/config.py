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

    @field_validator("database_url")
    @classmethod
    def require_asyncpg_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg://")
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

