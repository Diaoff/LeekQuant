"""Add strategy_source_snapshot column to backtest_results.

Capture the exact strategy source code executed at backtest time so that
historical backtests stay reproducible and comparable even after the
strategy row in `strategies` is edited in place (the table keeps no history,
and raw SQL backfills are not versioned in git). Without this column, every
strategy edit silently makes past backtest results uninterpretable.

Revision ID: 202608180001
Revises: 202608010001
Create Date: 2026-08-18 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202608180001"
down_revision: str | None = "202608010001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    "ALTER TABLE backtest_results ADD COLUMN strategy_source_snapshot TEXT",
]

DOWNGRADE_STATEMENTS = [
    "ALTER TABLE backtest_results DROP COLUMN IF EXISTS strategy_source_snapshot",
]


def upgrade() -> None:
    for stmt in UPGRADE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_STATEMENTS:
        op.execute(stmt)
