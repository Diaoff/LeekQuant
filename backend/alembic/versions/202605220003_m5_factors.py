"""Add M5 factor scoring tables.

Revision ID: 202605220003
Revises: 202605220002
Create Date: 2026-05-22 00:03:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605220003"
down_revision: str | None = "202605220002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BUILTIN_FACTOR_ROWS = """
    ('pe_ttm', 'PE TTM', 'valuation', 'stock_fundamentals.pe_ttm', -1, 1.000000, TRUE, '市盈率 TTM，越低越好'),
    ('pb', 'PB', 'valuation', 'stock_fundamentals.pb', -1, 1.000000, TRUE, '市净率，越低越好'),
    ('roe', 'ROE', 'quality', 'stock_fundamentals.roe', 1, 1.200000, TRUE, '净资产收益率，越高越好'),
    ('revenue_growth', 'Revenue Growth', 'growth', 'stock_fundamentals.revenue_growth', 1, 1.000000, TRUE, '营业收入同比增速，越高越好'),
    ('mom_20d', '20D Momentum', 'momentum', 'close / close_20d - 1', 1, 1.000000, TRUE, '20 个交易日动量，越高越好'),
    ('mom_60d', '60D Momentum', 'momentum', 'close / close_60d - 1', 1, 1.000000, TRUE, '60 个交易日动量，越高越好'),
    ('rsi6', 'RSI6', 'momentum', 'MyTT.RSI(close, 6)', 1, 0.800000, TRUE, '6 日 RSI，越高越强'),
    ('vol_20d', '20D Volatility', 'volatility', 'STD(returns, 20)', -1, 0.800000, TRUE, '20 个交易日收益波动率，越低越好')
"""


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE factor_definitions (
        name             VARCHAR(64) PRIMARY KEY,
        display_name     VARCHAR(100),
        category         VARCHAR(32) NOT NULL,
        expression       TEXT NOT NULL,
        direction        SMALLINT NOT NULL DEFAULT 1,
        default_weight   NUMERIC(10,6) NOT NULL DEFAULT 1,
        enabled          BOOLEAN NOT NULL DEFAULT TRUE,
        description      TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (direction IN (-1, 1)),
        CHECK (default_weight >= 0)
    )
    """,
    """
    CREATE TABLE factor_values (
        ts_code          VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        trade_date       DATE NOT NULL,
        factor_name      VARCHAR(64) NOT NULL REFERENCES factor_definitions(name),
        value            NUMERIC(20,8),
        normalized_value NUMERIC(20,8),
        percentile_rank  NUMERIC(10,8),
        data_source      VARCHAR(20) NOT NULL DEFAULT 'computed',
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (ts_code, trade_date, factor_name)
    )
    """,
    "CREATE INDEX idx_factor_values_date_name ON factor_values(trade_date, factor_name)",
    "CREATE INDEX idx_factor_values_name_date ON factor_values(factor_name, trade_date DESC)",
    """
    CREATE TABLE scoring_rank (
        id               BIGSERIAL PRIMARY KEY,
        trade_date       DATE NOT NULL,
        ts_code          VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        scope_type       VARCHAR(32) NOT NULL DEFAULT 'all',
        scope_value      VARCHAR(128),
        total_score      NUMERIC(20,8) NOT NULL,
        rank             INTEGER NOT NULL,
        percentile_rank  NUMERIC(10,8),
        factor_breakdown JSONB NOT NULL DEFAULT '{}'::JSONB,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (scope_type IN ('all', 'watchlist_group'))
    )
    """,
    """
    CREATE UNIQUE INDEX uq_scoring_rank_scope ON scoring_rank(
        trade_date,
        ts_code,
        scope_type,
        COALESCE(scope_value, '')
    )
    """,
    "CREATE INDEX idx_scoring_rank_date_rank ON scoring_rank(trade_date DESC, scope_type, rank)",
    "CREATE INDEX idx_scoring_rank_scope_date ON scoring_rank(scope_type, scope_value, trade_date DESC, rank)",
    """
    CREATE TABLE factor_analysis (
        id               BIGSERIAL PRIMARY KEY,
        factor_name      VARCHAR(64) NOT NULL REFERENCES factor_definitions(name),
        period_start     DATE NOT NULL,
        period_end       DATE NOT NULL,
        forward_days     INTEGER NOT NULL DEFAULT 5,
        ic               NUMERIC(14,8),
        ic_mean          NUMERIC(14,8),
        ic_std           NUMERIC(14,8),
        ir               NUMERIC(14,8),
        icir             NUMERIC(14,8),
        ic_gt_0_pct      NUMERIC(10,8),
        group_returns    JSONB NOT NULL DEFAULT '{}'::JSONB,
        details          JSONB NOT NULL DEFAULT '{}'::JSONB,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (forward_days > 0),
        UNIQUE (factor_name, period_start, period_end, forward_days)
    )
    """,
    "CREATE INDEX idx_factor_analysis_name_period ON factor_analysis(factor_name, period_start, period_end)",
    f"""
    INSERT INTO factor_definitions (
        name, display_name, category, expression, direction, default_weight, enabled, description
    )
    VALUES
    {BUILTIN_FACTOR_ROWS}
    ON CONFLICT (name) DO NOTHING
    """,
]


DOWNGRADE_STATEMENTS = [
    "DROP TABLE IF EXISTS factor_analysis",
    "DROP INDEX IF EXISTS idx_scoring_rank_scope_date",
    "DROP INDEX IF EXISTS idx_scoring_rank_date_rank",
    "DROP INDEX IF EXISTS uq_scoring_rank_scope",
    "DROP TABLE IF EXISTS scoring_rank",
    "DROP TABLE IF EXISTS factor_values",
    "DROP TABLE IF EXISTS factor_definitions",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
