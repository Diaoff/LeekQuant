"""Add missing index on trade_calendar(is_open, cal_date) for K-line sync perf.

The ``infer_incremental_kline_ranges`` query in ``service.py`` runs a correlated
subquery ``SELECT MIN(cal_date) FROM trade_calendar WHERE is_open = TRUE AND
cal_date > X`` for each stock (up to 5000 times). Without an index on
``(is_open, cal_date)``, each subquery triggers a sequential scan. This  adds
the missing covering index.

Also adds a composite index on ``stock_basic(is_delisted, symbol)`` for the
``infer_incremental_kline_ranges`` / ``infer_full_kline_ranges`` joins.

Revision ID: 202607270001
Revises: rebuild_kline_sync_queue
Create Date: 2026-07-27 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202607270001"
down_revision: str | None = "rebuild_kline_sync_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    # Covering index for the correlated subquery in infer_incremental_kline_ranges:
    # SELECT MIN(cal_date) FROM trade_calendar WHERE is_open = TRUE AND cal_date > X
    "CREATE INDEX IF NOT EXISTS idx_trade_calendar_open_date ON trade_calendar (is_open, cal_date)",
    # Covering index for stock_basic range scans (is_delisted=FALSE + ORDER BY symbol)
    "CREATE INDEX IF NOT EXISTS idx_stock_basic_active_symbol ON stock_basic (is_delisted, symbol) WHERE is_delisted = FALSE",
    # Covering index for get_sync_progress: filters by data_type='daily_kline',
    # groups by ts_code, and orders by last_trade_date
    "CREATE INDEX IF NOT EXISTS idx_data_update_state_kline_progress ON data_update_state (data_type, ts_code, last_trade_date) WHERE data_type = 'daily_kline'",
]


DOWNGRADE_STATEMENTS = [
    "DROP INDEX IF EXISTS idx_trade_calendar_open_date",
    "DROP INDEX IF EXISTS idx_stock_basic_active_symbol",
    "DROP INDEX IF EXISTS idx_data_update_state_kline_progress",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)