"""Add seesaw (跷跷板) tables: defensive_pool, market_signal_log, seesaw_trigger_log, defensive_rules.

Revision ID: 202608200003
Revises: 202608200001
Create Date: 2026-08-20 00:00:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202608200003"
down_revision: str | None = "202608200001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE defensive_pool (
        id           BIGSERIAL PRIMARY KEY,
        ts_code      VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        name         VARCHAR(64) NOT NULL,
        note         TEXT,
        tags         VARCHAR(256),
        sort_order   INTEGER NOT NULL DEFAULT 0,
        enabled      BOOLEAN NOT NULL DEFAULT TRUE,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_defensive_pool_enabled ON defensive_pool(enabled, sort_order)",
    "CREATE INDEX idx_defensive_pool_ts_code ON defensive_pool(ts_code)",
    """
    COMMENT ON TABLE defensive_pool IS '手动维护的跷跷板避险股票池'
    """,
    """
    CREATE TABLE market_signal_log (
        id               BIGSERIAL PRIMARY KEY,
        index_code       VARCHAR(10) NOT NULL DEFAULT '000300.SH',
        state            VARCHAR(16) NOT NULL CHECK (state IN ('up', 'neutral', 'down')),
        trigger_time     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        close_price      NUMERIC(12,4),
        prev_close       NUMERIC(12,4),
        change_pct       NUMERIC(10,6),
        ma20_gap         NUMERIC(10,6),
        ma60_gap         NUMERIC(10,6),
        drop_from_high   NUMERIC(10,6),
        condition_detail JSONB NOT NULL DEFAULT '{}'::JSONB,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_market_signal_index_time ON market_signal_log(index_code, trigger_time DESC)",
    """
    COMMENT ON TABLE market_signal_log IS '大盘状态变更记录（up/neutral/down）'
    """,
    """
    CREATE TABLE seesaw_trigger_log (
        id                BIGSERIAL PRIMARY KEY,
        trigger_time      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        market_state      VARCHAR(16) NOT NULL,
        index_code        VARCHAR(10) NOT NULL DEFAULT '000300.SH',
        recommended_count INTEGER NOT NULL DEFAULT 0,
        recommendations   JSONB NOT NULL DEFAULT '[]'::jsonb,
        subsequent_perf   JSONB,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_seesaw_trigger_time ON seesaw_trigger_log(trigger_time DESC)",
    """
    COMMENT ON TABLE seesaw_trigger_log IS '跷跷板触发记录，含推荐股票及后续表现'
    """,
    """
    CREATE TABLE defensive_rules (
        id                INTEGER PRIMARY KEY DEFAULT 1,
        index_code        VARCHAR(10) NOT NULL DEFAULT '000300.SH',
        ma_short          INTEGER NOT NULL DEFAULT 5,
        ma_long           INTEGER NOT NULL DEFAULT 20,
        ma_long2          INTEGER NOT NULL DEFAULT 60,
        drop_threshold    NUMERIC(10,6) NOT NULL DEFAULT '-0.03',
        high_window       INTEGER NOT NULL DEFAULT 20,
        high_drop_pct     NUMERIC(10,6) NOT NULL DEFAULT '-0.05',
        vol_expand_thresh NUMERIC(10,6),
        ma_cross_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
        enabled           BOOLEAN NOT NULL DEFAULT TRUE,
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    INSERT INTO defensive_rules (id, index_code, ma_short, ma_long, ma_long2,
        drop_threshold, high_window, high_drop_pct, enabled)
    VALUES (1, '000300.SH', 5, 20, 60, -0.03, 20, -0.05, TRUE)
    ON CONFLICT (id) DO NOTHING
    """,
]


DOWNGRADE_STATEMENTS = [
    "DROP TABLE IF EXISTS seesaw_trigger_log",
    "DROP TABLE IF EXISTS market_signal_log",
    "DROP TABLE IF EXISTS defensive_pool",
    "DROP TABLE IF EXISTS defensive_rules",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
