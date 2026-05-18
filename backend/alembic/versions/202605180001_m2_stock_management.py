"""Add M2 stock management tables.

Revision ID: 202605180001
Revises: 202605150001
Create Date: 2026-05-18 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605180001"
down_revision: str | None = "202605150001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    INSERT INTO users (id, username, password_hash, display_name, is_active)
    VALUES (1, 'local', 'local-no-auth', 'Local User', TRUE)
    ON CONFLICT (username) DO NOTHING
    """,
    """
    SELECT setval(
        pg_get_serial_sequence('users', 'id'),
        GREATEST((SELECT COALESCE(MAX(id), 1) FROM users), 1),
        TRUE
    )
    """,
    """
    CREATE TABLE stock_fundamentals (
        ts_code             VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        report_date         DATE NOT NULL,
        announce_date       DATE,
        pe_ttm              NUMERIC(12,4),
        pb                  NUMERIC(12,4),
        ps_ttm              NUMERIC(12,4),
        pcf_ttm             NUMERIC(12,4),
        roe                 NUMERIC(12,6),
        roa                 NUMERIC(12,6),
        market_cap          NUMERIC(20,4),
        float_market_cap    NUMERIC(20,4),
        dividend_yield      NUMERIC(12,6),
        revenue             NUMERIC(20,4),
        net_profit          NUMERIC(20,4),
        revenue_growth      NUMERIC(12,6),
        net_profit_growth   NUMERIC(12,6),
        gross_margin        NUMERIC(12,6),
        debt_to_equity      NUMERIC(12,6),
        current_ratio       NUMERIC(12,6),
        free_cash_flow      NUMERIC(20,4),
        income_statement    JSONB,
        balance_sheet       JSONB,
        cashflow_statement  JSONB,
        data_source         VARCHAR(20) NOT NULL DEFAULT 'baostock',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (ts_code, report_date)
    )
    """,
    "CREATE INDEX idx_fundamentals_report_date ON stock_fundamentals(report_date DESC)",
    "CREATE INDEX idx_fundamentals_code_date ON stock_fundamentals(ts_code, report_date DESC)",
    """
    CREATE TABLE watchlist (
        id            BIGSERIAL PRIMARY KEY,
        user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        group_name    VARCHAR(64) NOT NULL DEFAULT '默认',
        ts_code       VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        sort_order    INTEGER NOT NULL DEFAULT 0,
        note          TEXT,
        added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, group_name, ts_code)
    )
    """,
    "CREATE INDEX idx_watchlist_user_group ON watchlist(user_id, group_name, sort_order)",
    """
    CREATE TABLE stock_pools (
        id            BIGSERIAL PRIMARY KEY,
        user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name          VARCHAR(100) NOT NULL,
        description   TEXT,
        filters       JSONB NOT NULL DEFAULT '{}'::JSONB,
        is_dynamic    BOOLEAN NOT NULL DEFAULT TRUE,
        last_built_at TIMESTAMPTZ,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_stock_pools_user ON stock_pools(user_id, updated_at DESC)",
    """
    CREATE TABLE stock_pool_items (
        pool_id        BIGINT NOT NULL REFERENCES stock_pools(id) ON DELETE CASCADE,
        ts_code        VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        score          NUMERIC(12,6),
        reason         JSONB,
        added_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (pool_id, ts_code)
    )
    """,
    "CREATE INDEX idx_pool_items_code ON stock_pool_items(ts_code)",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in [
        "DROP TABLE IF EXISTS stock_pool_items",
        "DROP TABLE IF EXISTS stock_pools",
        "DROP TABLE IF EXISTS watchlist",
        "DROP TABLE IF EXISTS stock_fundamentals",
        "DELETE FROM users WHERE id = 1 AND username = 'local'",
    ]:
        op.execute(statement)
