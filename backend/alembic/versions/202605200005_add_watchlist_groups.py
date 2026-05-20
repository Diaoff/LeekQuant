"""Add persistent watchlist groups.

Revision ID: 202605200005
Revises: 202605200004
Create Date: 2026-05-20 00:05:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605200005"
down_revision: str | None = "202605200004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE watchlist_groups (
        id            BIGSERIAL PRIMARY KEY,
        user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        group_name    VARCHAR(64) NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, group_name)
    )
    """,
    "CREATE INDEX idx_watchlist_groups_user ON watchlist_groups(user_id, updated_at DESC)",
    """
    INSERT INTO watchlist_groups (user_id, group_name)
    SELECT DISTINCT user_id, group_name
    FROM watchlist
    ON CONFLICT (user_id, group_name) DO NOTHING
    """,
    """
    INSERT INTO watchlist_groups (user_id, group_name)
    VALUES (1, '默认')
    ON CONFLICT (user_id, group_name) DO NOTHING
    """,
    "COMMENT ON TABLE watchlist_groups IS '自选股分组表，支持空分组和分组维护'",
    "COMMENT ON COLUMN watchlist_groups.id IS '自选股分组主键'",
    "COMMENT ON COLUMN watchlist_groups.user_id IS '本地用户ID，关联 users.id'",
    "COMMENT ON COLUMN watchlist_groups.group_name IS '自选股分组名称，如 默认 / 价投 / 观察股'",
    "COMMENT ON COLUMN watchlist_groups.created_at IS '分组创建时间'",
    "COMMENT ON COLUMN watchlist_groups.updated_at IS '分组最后更新时间'",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS watchlist_groups")
