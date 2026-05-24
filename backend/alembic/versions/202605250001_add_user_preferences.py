"""Add user preferences table.

Revision ID: 202605250001
Revises: 202605240001
Create Date: 2026-05-25 10:00:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605250001"
down_revision: str | None = "202605240001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE user_preferences (
        user_id    BIGINT NOT NULL,
        key        VARCHAR(64) NOT NULL,
        value      JSONB NOT NULL DEFAULT '{}'::JSONB,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, key)
    )
    """,
    "COMMENT ON TABLE user_preferences IS '本地用户偏好设置'",
    "COMMENT ON COLUMN user_preferences.user_id IS '本地用户ID'",
    "COMMENT ON COLUMN user_preferences.key IS '偏好键，如 trading_fee'",
    "COMMENT ON COLUMN user_preferences.value IS '偏好配置 JSON'",
    "COMMENT ON COLUMN user_preferences.updated_at IS '最后更新时间'",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_preferences")
