"""Remove stock pool tables and references.

Revision ID: 202605200004
Revises: 202605200003
Create Date: 2026-05-20 00:04:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605200004"
down_revision: str | None = "202605200003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    "ALTER TABLE backtest_results DROP CONSTRAINT IF EXISTS backtest_results_pool_id_fkey",
    "ALTER TABLE strategies DROP CONSTRAINT IF EXISTS strategies_pool_id_fkey",
    "ALTER TABLE backtest_results DROP COLUMN IF EXISTS pool_id",
    "ALTER TABLE strategies DROP COLUMN IF EXISTS pool_id",
    "DROP TABLE IF EXISTS stock_pool_items",
    "DROP TABLE IF EXISTS stock_pools",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in [
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
        """
        ALTER TABLE strategies
        ADD COLUMN pool_id BIGINT REFERENCES stock_pools(id) ON DELETE SET NULL
        """,
        """
        ALTER TABLE backtest_results
        ADD COLUMN pool_id BIGINT REFERENCES stock_pools(id) ON DELETE SET NULL
        """,
    ]:
        op.execute(statement)
