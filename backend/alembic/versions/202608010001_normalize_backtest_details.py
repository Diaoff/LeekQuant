"""Normalize backtest trade/lot/ranking details into separate tables.

Split the oversized JSONB columns (trade_records, pnl_analysis.closed_lots,
pnl_analysis.stock_rankings) out of backtest_results into dedicated child
tables to avoid the PostgreSQL single-value JSONB 256MB limit.

Revision ID: 202608010001
Revises: 202607310002
Create Date: 2026-08-01 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202608010001"
down_revision: str | None = "202607310002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE backtest_trades (
        id              BIGSERIAL PRIMARY KEY,
        backtest_id     BIGINT NOT NULL REFERENCES backtest_results(id) ON DELETE CASCADE,
        seq             INTEGER NOT NULL,
        ts_code         VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        trade_date      DATE NOT NULL,
        direction       VARCHAR(4) NOT NULL,
        price           NUMERIC(12,4) NOT NULL,
        volume          INTEGER NOT NULL,
        amount          NUMERIC(20,4) NOT NULL,
        fee             NUMERIC(20,4) NOT NULL DEFAULT 0,
        stamp_tax       NUMERIC(20,4) NOT NULL DEFAULT 0,
        transfer_fee    NUMERIC(20,4) NOT NULL DEFAULT 0,
        slippage        NUMERIC(20,4) NOT NULL DEFAULT 0,
        signal_type     VARCHAR(10) NOT NULL DEFAULT '',
        action          VARCHAR(20) NOT NULL DEFAULT '',
        signal_reason   TEXT NOT NULL DEFAULT '',
        target_position NUMERIC(8,4) NOT NULL DEFAULT 0,
        position_before NUMERIC(8,4) NOT NULL DEFAULT 0,
        position_after  NUMERIC(8,4) NOT NULL DEFAULT 0,
        pnl             NUMERIC(20,4) NOT NULL DEFAULT 0,
        balance_before  NUMERIC(20,4) NOT NULL DEFAULT 0,
        balance_after   NUMERIC(20,4) NOT NULL DEFAULT 0,
        holding_days    INTEGER NOT NULL DEFAULT 0,
        exit_reason     VARCHAR(20) NOT NULL DEFAULT '',
        UNIQUE (backtest_id, seq)
    )
    """,
    "CREATE INDEX idx_backtest_trades_backtest ON backtest_trades(backtest_id, seq)",
    """
    CREATE TABLE backtest_closed_lots (
        id              BIGSERIAL PRIMARY KEY,
        backtest_id     BIGINT NOT NULL REFERENCES backtest_results(id) ON DELETE CASCADE,
        seq             INTEGER NOT NULL,
        ts_code         VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        shares          INTEGER NOT NULL,
        entry_price     NUMERIC(12,4) NOT NULL,
        entry_date      DATE NOT NULL,
        exit_price      NUMERIC(12,4) NOT NULL,
        exit_date       DATE NOT NULL,
        entry_fee       NUMERIC(20,4) NOT NULL DEFAULT 0,
        exit_fee        NUMERIC(20,4) NOT NULL DEFAULT 0,
        gross_pnl       NUMERIC(20,4) NOT NULL DEFAULT 0,
        net_pnl         NUMERIC(20,4) NOT NULL DEFAULT 0,
        return_rate     NUMERIC(14,8) NOT NULL DEFAULT 0,
        holding_days    INTEGER NOT NULL DEFAULT 0,
        exit_reason     VARCHAR(20) NOT NULL DEFAULT '',
        UNIQUE (backtest_id, seq)
    )
    """,
    "CREATE INDEX idx_backtest_closed_lots_backtest ON backtest_closed_lots(backtest_id, seq)",
    """
    CREATE TABLE backtest_stock_rankings (
        id              BIGSERIAL PRIMARY KEY,
        backtest_id     BIGINT NOT NULL REFERENCES backtest_results(id) ON DELETE CASCADE,
        seq             INTEGER NOT NULL,
        ts_code         VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        trade_count     INTEGER NOT NULL DEFAULT 0,
        total_pnl       NUMERIC(20,4) NOT NULL DEFAULT 0,
        win_rate        NUMERIC(8,4) NOT NULL DEFAULT 0,
        avg_return      NUMERIC(14,8) NOT NULL DEFAULT 0,
        max_profit      NUMERIC(20,4) NOT NULL DEFAULT 0,
        max_loss        NUMERIC(20,4) NOT NULL DEFAULT 0,
        UNIQUE (backtest_id, seq)
    )
    """,
    "CREATE INDEX idx_backtest_stock_rankings_backtest ON backtest_stock_rankings(backtest_id, seq)",
]


DOWNGRADE_STATEMENTS = [
    "DROP TABLE IF EXISTS backtest_stock_rankings",
    "DROP TABLE IF EXISTS backtest_closed_lots",
    "DROP TABLE IF EXISTS backtest_trades",
]


def upgrade() -> None:
    for stmt in UPGRADE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_STATEMENTS:
        op.execute(stmt)
