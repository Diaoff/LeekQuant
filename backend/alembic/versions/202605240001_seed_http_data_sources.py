"""Seed HTTP data source plugins.

Revision ID: 202605240001
Revises: 202605220003
Create Date: 2026-05-24 10:45:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605240001"
down_revision: str | None = "202605220003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    INSERT INTO data_source_config (name, display_name, priority, enabled)
    VALUES
        ('eastmoney_http', 'EastMoney HTTP', 1, TRUE),
        ('tencent_http', 'Tencent Finance HTTP', 2, TRUE),
        ('mootdx', 'Mootdx', 5, FALSE)
    ON CONFLICT (name) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        updated_at = NOW()
    """,
    """
    UPDATE data_source_config
    SET priority = CASE name
        WHEN 'adata' THEN GREATEST(priority, 10)
        WHEN 'baostock' THEN GREATEST(priority, 20)
        WHEN 'akshare' THEN GREATEST(priority, 30)
        ELSE priority
    END
    WHERE name IN ('adata', 'baostock', 'akshare')
    """,
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DELETE FROM data_source_config WHERE name IN ('eastmoney_http', 'tencent_http', 'mootdx')")
