"""Drop the data_update_state table and its index.

The circuit breaker / provider-health mechanism that depended on
``data_update_state.failure_count`` has been removed (see fetcher.py /
circuit_breaker removal). K-line sync progress is now computed directly from
``daily_kline`` (source of truth) and per-item retry / permanent-failure is
handled by the ``kline_sync_jobs`` / ``kline_sync_items`` DB queue. This table
is no longer written to or read by any code path, so it is dropped.

Revision ID: 202607290001
Revises: 202607270001
Create Date: 2026-07-29 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202607290001"
down_revision: str | None = "202607270001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the index that the 202607270001 migration created on this table
    # before dropping the table itself.
    op.execute(
        "DROP INDEX IF EXISTS idx_data_update_state_kline_progress"
    )
    op.execute("DROP TABLE IF EXISTS data_update_state")


def downgrade() -> None:
    # This removal is intentional and one-way; the table is no longer used by
    # any code path, so we do not recreate it on downgrade.
    pass
