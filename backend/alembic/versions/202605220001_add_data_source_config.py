"""Add data_source_config table for configurable provider order.

Revision ID: 202605220001
Revises: 202605200005
Create Date: 2026-05-22 01:00:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605220001"
down_revision: str | None = "202605200005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE data_source_config (
        id           BIGSERIAL PRIMARY KEY,
        name         VARCHAR(50) NOT NULL UNIQUE,
        display_name VARCHAR(100) NOT NULL,
        priority     INTEGER NOT NULL,
        enabled      BOOLEAN NOT NULL DEFAULT TRUE,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    INSERT INTO data_source_config (name, display_name, priority, enabled) VALUES
        ('eastmoney_http', 'EastMoney HTTP', 1, TRUE),
        ('tencent_http', 'Tencent Finance HTTP', 2, TRUE),
        ('mootdx', 'Mootdx', 5, FALSE),
        ('adata', 'AData', 10, TRUE),
        ('baostock', 'Baostock', 20, TRUE),
        ('akshare', 'AkShare', 30, TRUE)
    """,
    "COMMENT ON TABLE data_source_config IS '数据源配置，支持调整优先级和启用/禁用'",
    "COMMENT ON COLUMN data_source_config.name IS '数据源插件标识，如 eastmoney_http / tencent_http / adata / baostock / akshare'",
    "COMMENT ON COLUMN data_source_config.display_name IS '显示名称'",
    "COMMENT ON COLUMN data_source_config.priority IS '优先级（小→大）'",
    "COMMENT ON COLUMN data_source_config.enabled IS '是否启用'",
    "COMMENT ON COLUMN data_source_config.updated_at IS '最后更新时间'",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_source_config")
