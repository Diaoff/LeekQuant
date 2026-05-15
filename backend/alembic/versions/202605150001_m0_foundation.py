"""Create M0 foundation tables.

Revision ID: 202605150001
Revises:
Create Date: 2026-05-15 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605150001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE users (
        id              BIGSERIAL PRIMARY KEY,
        username        VARCHAR(64) UNIQUE NOT NULL,
        password_hash   VARCHAR(255) NOT NULL,
        display_name    VARCHAR(64),
        is_active       BOOLEAN NOT NULL DEFAULT TRUE,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE stock_basic (
        ts_code        VARCHAR(10) PRIMARY KEY,
        symbol         VARCHAR(6) NOT NULL,
        name           VARCHAR(64) NOT NULL,
        market         VARCHAR(16),
        exchange       VARCHAR(8),
        industry       VARCHAR(64),
        area           VARCHAR(32),
        list_date      DATE,
        delist_date    DATE,
        is_st          BOOLEAN NOT NULL DEFAULT FALSE,
        is_delisted    BOOLEAN NOT NULL DEFAULT FALSE,
        data_source    VARCHAR(20) NOT NULL DEFAULT 'adata',
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_stock_basic_market ON stock_basic(market)",
    "CREATE INDEX idx_stock_basic_industry ON stock_basic(industry)",
    "CREATE INDEX idx_stock_basic_status ON stock_basic(is_st, is_delisted)",
    """
    CREATE TABLE trade_calendar (
        cal_date        DATE PRIMARY KEY,
        is_open         BOOLEAN NOT NULL,
        pretrade_date   DATE,
        nexttrade_date  DATE,
        is_weekend      BOOLEAN NOT NULL DEFAULT FALSE,
        is_holiday      BOOLEAN NOT NULL DEFAULT FALSE,
        source          VARCHAR(20) NOT NULL DEFAULT 'akshare',
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE daily_kline (
        ts_code         VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        trade_date      DATE NOT NULL,
        open            NUMERIC(12,4),
        high            NUMERIC(12,4),
        low             NUMERIC(12,4),
        close           NUMERIC(12,4),
        pre_close       NUMERIC(12,4),
        volume          BIGINT,
        amount          NUMERIC(20,4),
        turnover_rate   NUMERIC(12,6),
        adj_factor      NUMERIC(18,8),
        is_suspended    BOOLEAN NOT NULL DEFAULT FALSE,
        is_limit_up     BOOLEAN NOT NULL DEFAULT FALSE,
        is_limit_down   BOOLEAN NOT NULL DEFAULT FALSE,
        data_source     VARCHAR(20) NOT NULL,
        raw_payload     JSONB,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (ts_code, trade_date)
    ) PARTITION BY RANGE (trade_date)
    """,
    """
    CREATE TABLE daily_kline_2020 PARTITION OF daily_kline
        FOR VALUES FROM ('2020-01-01') TO ('2021-01-01')
    """,
    """
    CREATE TABLE daily_kline_2021 PARTITION OF daily_kline
        FOR VALUES FROM ('2021-01-01') TO ('2022-01-01')
    """,
    """
    CREATE TABLE daily_kline_2022 PARTITION OF daily_kline
        FOR VALUES FROM ('2022-01-01') TO ('2023-01-01')
    """,
    """
    CREATE TABLE daily_kline_2023 PARTITION OF daily_kline
        FOR VALUES FROM ('2023-01-01') TO ('2024-01-01')
    """,
    """
    CREATE TABLE daily_kline_2024 PARTITION OF daily_kline
        FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')
    """,
    """
    CREATE TABLE daily_kline_2025 PARTITION OF daily_kline
        FOR VALUES FROM ('2025-01-01') TO ('2026-01-01')
    """,
    """
    CREATE TABLE daily_kline_2026 PARTITION OF daily_kline
        FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')
    """,
    """
    CREATE TABLE daily_kline_2027 PARTITION OF daily_kline
        FOR VALUES FROM ('2027-01-01') TO ('2028-01-01')
    """,
    "CREATE INDEX idx_daily_kline_date ON daily_kline(trade_date)",
    "CREATE INDEX idx_daily_kline_code_date_desc ON daily_kline(ts_code, trade_date DESC)",
    """
    CREATE TABLE data_update_state (
        id                BIGSERIAL PRIMARY KEY,
        data_type         VARCHAR(32) NOT NULL,
        ts_code           VARCHAR(10),
        source            VARCHAR(20),
        last_trade_date   DATE,
        last_success_at   TIMESTAMPTZ,
        last_failure_at   TIMESTAMPTZ,
        failure_count     INTEGER NOT NULL DEFAULT 0,
        error_message     TEXT,
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (data_type, ts_code, source)
    )
    """,
    """
    CREATE TABLE task_runs (
        id                BIGSERIAL PRIMARY KEY,
        task_name         VARCHAR(128) NOT NULL,
        task_id           VARCHAR(128),
        status            VARCHAR(20) NOT NULL,
        started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at       TIMESTAMPTZ,
        duration_ms       INTEGER,
        payload           JSONB NOT NULL DEFAULT '{}'::JSONB,
        result            JSONB NOT NULL DEFAULT '{}'::JSONB,
        error_message     TEXT,
        CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled'))
    )
    """,
    "CREATE INDEX idx_task_runs_name_started ON task_runs(task_name, started_at DESC)",
    """
    CREATE TABLE alert_events (
        id                BIGSERIAL PRIMARY KEY,
        level             VARCHAR(16) NOT NULL,
        category          VARCHAR(32) NOT NULL,
        title             VARCHAR(200) NOT NULL,
        message           TEXT,
        payload           JSONB NOT NULL DEFAULT '{}'::JSONB,
        is_resolved       BOOLEAN NOT NULL DEFAULT FALSE,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        resolved_at       TIMESTAMPTZ,
        CHECK (level IN ('info', 'warning', 'error', 'critical'))
    )
    """,
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in [
        "DROP TABLE IF EXISTS alert_events",
        "DROP TABLE IF EXISTS task_runs",
        "DROP TABLE IF EXISTS data_update_state",
        "DROP TABLE IF EXISTS daily_kline",
        "DROP TABLE IF EXISTS trade_calendar",
        "DROP TABLE IF EXISTS stock_basic",
        "DROP TABLE IF EXISTS users",
    ]:
        op.execute(statement)
