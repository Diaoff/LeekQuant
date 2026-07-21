"""Add daily_kline partitions for 2028-2030 plus DEFAULT partition.

Ensures writes beyond 2027 do not fail with "no partition of relation
found". DEFAULT partition catches any future date that lacks an explicit
partition.

Revision ID: 202607200001
Revises: 202605250001
Create Date: 2026-07-20 10:00:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202607200001"
down_revision: str | None = "202605250001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS daily_kline_2028 PARTITION OF daily_kline
        FOR VALUES FROM ('2028-01-01') TO ('2029-01-01')
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_kline_2029 PARTITION OF daily_kline
        FOR VALUES FROM ('2029-01-01') TO ('2030-01-01')
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_kline_2030 PARTITION OF daily_kline
        FOR VALUES FROM ('2030-01-01') TO ('2031-01-01')
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_kline_default PARTITION OF daily_kline
        DEFAULT
    """,
]

DOWNGRADE_STATEMENTS = [
    "DROP TABLE IF EXISTS daily_kline_default",
    "DROP TABLE IF EXISTS daily_kline_2030",
    "DROP TABLE IF EXISTS daily_kline_2029",
    "DROP TABLE IF EXISTS daily_kline_2028",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
