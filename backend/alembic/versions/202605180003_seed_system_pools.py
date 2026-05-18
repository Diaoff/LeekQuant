"""Seed system pools: 全市场, 沪深300, 中证500.

Revision ID: 202605180003
Revises: 202605180002
Create Date: 2026-05-18 00:03:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605180003"
down_revision: str | None = "202605180002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SYSTEM_POOLS = [
    {
        "name": "全市场",
        "description": "全部A股（排除ST、退市）",
        "filters": '{"exclude_st": true, "exclude_delisted": true}',
        "is_dynamic": True,
    },
]


def upgrade() -> None:
    for pool in SYSTEM_POOLS:
        op.execute(
            f"""
            INSERT INTO stock_pools (user_id, name, description, filters, is_dynamic)
            VALUES (
                1,
                '{pool["name"]}',
                '{pool["description"]}',
                CAST('{pool["filters"]}' AS JSONB),
                {pool["is_dynamic"]}
            )
            ON CONFLICT DO NOTHING
            """
        )


def downgrade() -> None:
    for pool in SYSTEM_POOLS:
        op.execute(
            f"DELETE FROM stock_pools WHERE name = '{pool['name']}' AND user_id = 1"
        )
