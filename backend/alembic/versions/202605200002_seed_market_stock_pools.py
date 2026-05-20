"""Seed market-segment stock pools.

Revision ID: 202605200002
Revises: 202605200001
Create Date: 2026-05-20 00:02:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605200002"
down_revision: str | None = "202605200001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SYSTEM_POOLS = [
    {
        "name": "主板",
        "description": "A股主板（排除ST、退市）",
        "filters": '{"market": "主板", "exclude_st": true, "exclude_delisted": true}',
        "market": "主板",
    },
    {
        "name": "创业板",
        "description": "创业板股票（排除ST、退市）",
        "filters": '{"market": "创业板", "exclude_st": true, "exclude_delisted": true}',
        "market": "创业板",
    },
    {
        "name": "科创板",
        "description": "科创板股票（排除ST、退市）",
        "filters": '{"market": "科创板", "exclude_st": true, "exclude_delisted": true}',
        "market": "科创板",
    },
    {
        "name": "北交所",
        "description": "北交所股票（排除ST、退市）",
        "filters": '{"market": "北交所", "exclude_st": true, "exclude_delisted": true}',
        "market": "北交所",
    },
]


BACKFILL_STOCK_BASIC_MARKET_SQL = """
UPDATE stock_basic
SET market = CASE
    WHEN COALESCE(NULLIF(symbol, ''), split_part(ts_code, '.', 1)) LIKE '688%'
      OR COALESCE(NULLIF(symbol, ''), split_part(ts_code, '.', 1)) LIKE '689%' THEN '科创板'
    WHEN COALESCE(NULLIF(symbol, ''), split_part(ts_code, '.', 1)) LIKE '300%'
      OR COALESCE(NULLIF(symbol, ''), split_part(ts_code, '.', 1)) LIKE '301%' THEN '创业板'
    WHEN COALESCE(NULLIF(symbol, ''), split_part(ts_code, '.', 1)) LIKE '4%'
      OR COALESCE(NULLIF(symbol, ''), split_part(ts_code, '.', 1)) LIKE '8%' THEN '北交所'
    ELSE '主板'
END
WHERE market IS NULL OR market = ''
"""


def upgrade() -> None:
    op.execute(BACKFILL_STOCK_BASIC_MARKET_SQL)
    for pool in SYSTEM_POOLS:
        op.execute(
            f"""
            INSERT INTO stock_pools (user_id, name, description, filters, is_dynamic)
            SELECT
                1,
                '{pool["name"]}',
                '{pool["description"]}',
                CAST('{pool["filters"]}' AS JSONB),
                TRUE
            WHERE NOT EXISTS (
                SELECT 1 FROM stock_pools
                WHERE user_id = 1 AND name = '{pool["name"]}'
            )
            """
        )
        op.execute(
            f"""
            INSERT INTO stock_pool_items (pool_id, ts_code, reason)
            SELECT
                p.id,
                s.ts_code,
                jsonb_build_object('filters', p.filters)
            FROM stock_pools p
            JOIN stock_basic s ON s.market = '{pool["market"]}'
            WHERE p.user_id = 1
              AND p.name = '{pool["name"]}'
              AND s.is_st = FALSE
              AND s.is_delisted = FALSE
            ON CONFLICT (pool_id, ts_code) DO UPDATE SET
                reason = EXCLUDED.reason,
                added_at = NOW()
            """
        )
        op.execute(
            f"""
            UPDATE stock_pools
            SET last_built_at = NOW(),
                updated_at = NOW()
            WHERE user_id = 1
              AND name = '{pool["name"]}'
              AND EXISTS (
                  SELECT 1 FROM stock_pool_items
                  WHERE pool_id = stock_pools.id
              )
            """
        )


def downgrade() -> None:
    for pool in SYSTEM_POOLS:
        op.execute(
            f"DELETE FROM stock_pools WHERE user_id = 1 AND name = '{pool['name']}'"
        )
