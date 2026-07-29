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
    full_kline_sync_concurrency: int = Field(default=8, alias="FULL_KLINE_SYNC_CONCURRENCY")
    strategy_exec_timeout_seconds: float = Field(default=2.0, alias="STRATEGY_EXEC_TIMEOUT_SECONDS")
    strategy_exec_memory_mb: int = Field(default=256, alias="STRATEGY_EXEC_MEMORY_MB")
    strategy_exec_traceback_chars: int = Field(default=4000, alias="STRATEGY_EXEC_TRACEBACK_CHARS")
    strategy_default_inline: bool = Field(default=True, alias="STRATEGY_DEFAULT_INLINE")
    backtest_adjust_mode: str = Field(default="qfq", alias="BACKTEST_ADJUST_MODE")
    backtest_fill_price_mode: str = Field(default="next_open", alias="BACKTEST_FILL_PRICE_MODE")
    realtime_bus_persistence: bool = Field(default=True, alias="REALTIME_BUS_PERSISTENCE")
    ws_queue_maxsize: int = Field(default=100, alias="WS_QUEUE_MAXSIZE")
    ws_send_timeout_seconds: float = Field(default=5.0, alias="WS_SEND_TIMEOUT_SECONDS")
    ws_ping_interval_seconds: float = Field(default=20.0, alias="WS_PING_INTERVAL_SECONDS")
    ws_ping_timeout_seconds: float = Field(default=5.0, alias="WS_PING_TIMEOUT_SECONDS")
    stale_task_run_hours: int = Field(default=2, alias="STALE_TASK_RUN_HOURS")
    beat_lock_ttl_seconds: int = Field(default=1860, alias="BEAT_LOCK_TTL_SECONDS")
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=5, alias="DB_MAX_OVERFLOW")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")
    data_max_retries: int = Field(default=3, alias="DATA_MAX_RETRIES")
    # Celery task time limits (seconds). The app-wide defaults bound any task
    # that does not set its own limit.
    celery_task_soft_time_limit: int = Field(
        default=1500, alias="CELERY_TASK_SOFT_TIME_LIMIT"
    )
    celery_task_time_limit: int = Field(
        default=1800, alias="CELERY_TASK_TIME_LIMIT"
    )
    # --- K-line sync (DB queue architecture) ---
    kline_per_stock_timeout_seconds: int = Field(default=30, description="单只股票 fetch_with_fallback 的超时秒数,防止单股拖垮整个 worker")
    kline_sync_max_attempts: int = Field(
        default=50, alias="KLINE_PERMANENT_FAILURE_THRESHOLD",
        description="单 ts_code 连续失败次数达到此阈值时标记为永久失败,不再重投",
    )
    kline_sync_worker_count: int = Field(default=4, alias="KLINE_SYNC_WORKER_COUNT")
    kline_sync_worker_concurrency: int = Field(default=8, alias="KLINE_SYNC_WORKER_CONCURRENCY")
    kline_sync_worker_budget_seconds: int = Field(default=3600, alias="KLINE_SYNC_WORKER_BUDGET_SECONDS")
    kline_sync_stuck_seconds: int = Field(default=180, alias="KLINE_SYNC_STUCK_SECONDS")
    kline_sync_recover_interval_seconds: int = Field(default=60, alias="KLINE_SYNC_RECOVER_INTERVAL_SECONDS")
    # Test-only: limit number of stocks processed in a dispatch (None = unlimited).
    # Set to a small number (e.g. 100) to validate the queue end-to-end without
    # pulling the full universe (~5000 stocks). Set to None or 0 in production.
    kline_sync_test_limit: int | None = Field(default=None, alias="KLINE_SYNC_TEST_LIMIT")

    @field_validator("database_url")
    @classmethod
    def require_asyncpg_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg://")
        return value

    @field_validator("full_kline_sync_concurrency")
    @classmethod
    def validate_full_kline_sync_concurrency(cls, value: int) -> int:
        if not 1 <= value <= 16:
            raise ValueError("FULL_KLINE_SYNC_CONCURRENCY must be between 1 and 16")
        return value

    @field_validator("celery_task_soft_time_limit")
    @classmethod
    def validate_celery_task_soft_time_limit(cls, value: int) -> int:
        if not 60 <= value <= 7200:
            raise ValueError("CELERY_TASK_SOFT_TIME_LIMIT must be between 60 and 7200")
        return value

    @field_validator("celery_task_time_limit")
    @classmethod
    def validate_celery_task_time_limit(cls, value: int) -> int:
        if not 120 <= value <= 10800:
            raise ValueError("CELERY_TASK_TIME_LIMIT must be between 120 and 10800")
        return value

    @field_validator("kline_sync_worker_count")
    @classmethod
    def validate_kline_sync_worker_count(cls, value: int) -> int:
        if not 1 <= value <= 16:
            raise ValueError("KLINE_SYNC_WORKER_COUNT must be between 1 and 16")
        return value

    @field_validator("kline_sync_worker_concurrency")
    @classmethod
    def validate_kline_sync_worker_concurrency(cls, value: int) -> int:
        if not 1 <= value <= 16:
            raise ValueError("KLINE_SYNC_WORKER_CONCURRENCY must be between 1 and 16")
        return value

    @field_validator("kline_sync_worker_budget_seconds")
    @classmethod
    def validate_kline_sync_worker_budget_seconds(cls, value: int) -> int:
        if not 60 <= value <= 7200:
            raise ValueError("KLINE_SYNC_WORKER_BUDGET_SECONDS must be between 60 and 7200")
        return value

    @field_validator("kline_sync_stuck_seconds")
    @classmethod
    def validate_kline_sync_stuck_seconds(cls, value: int) -> int:
        if not 60 <= value <= 3600:
            raise ValueError("KLINE_SYNC_STUCK_SECONDS must be between 60 and 3600")
        return value

    @field_validator("kline_sync_test_limit")
    @classmethod
    def validate_kline_sync_test_limit(cls, value: int | None) -> int | None:
        # Treat 0 or negative as "no limit" for production convenience.
        if value is not None and value <= 0:
            return None
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

    @field_validator("backtest_adjust_mode")
    @classmethod
    def validate_backtest_adjust_mode(cls, value: str) -> str:
        allowed = {"qfq", "hfq", "none"}
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"BACKTEST_ADJUST_MODE must be one of {sorted(allowed)}, got {value!r}")
        return normalized

    @field_validator("backtest_fill_price_mode")
    @classmethod
    def validate_backtest_fill_price_mode(cls, value: str) -> str:
        allowed = {"next_open", "current_close", "current_intraday"}
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(
                f"BACKTEST_FILL_PRICE_MODE must be one of {sorted(allowed)}, got {value!r}"
            )
        return normalized

    @property
    def kline_permanent_failure_threshold(self) -> int:
        """Backward compat alias for kline_sync_max_attempts."""
        return self.kline_sync_max_attempts

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
