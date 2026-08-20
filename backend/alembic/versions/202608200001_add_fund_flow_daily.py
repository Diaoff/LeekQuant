"""Add fund_flow_daily table for daily main force capital flow data.

Revision ID: 202608200001
Revises: 202608180001
Create Date: 2026-08-20 00:00:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202608200001"
down_revision: str | None = "202608180001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE fund_flow_daily (
        ts_code          VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        trade_date       DATE NOT NULL,
        main_net_amount  NUMERIC(20,0),
        main_net_ratio   NUMERIC(10,6),
        ultra_net_amount NUMERIC(20,0),
        ultra_net_ratio  NUMERIC(10,6),
        large_net_amount NUMERIC(20,0),
        large_net_ratio  NUMERIC(10,6),
        mid_net_amount   NUMERIC(20,0),
        mid_net_ratio    NUMERIC(10,6),
        small_net_amount NUMERIC(20,0),
        small_net_ratio  NUMERIC(10,6),
        data_source      VARCHAR(20) NOT NULL DEFAULT 'akshare',
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    "CREATE INDEX idx_fund_flow_ts_date ON fund_flow_daily(ts_code, trade_date DESC)",
    """
    COMMENT ON TABLE fund_flow_daily IS '日频主力资金流向数据，来源：AkShare stock_individual_fund_flow'
    """,
    """
    COMMENT ON COLUMN fund_flow_daily.main_net_amount IS '主力净流入净额（元），超大单+大单合计'
    """,
    """
    COMMENT ON COLUMN fund_flow_daily.main_net_ratio IS '主力净流入净占比（%）'
    """,
    """
    COMMENT ON COLUMN fund_flow_daily.ultra_net_amount IS '超大单净流入净额（元）'
    """,
    """
    COMMENT ON COLUMN fund_flow_daily.large_net_amount IS '大单净流入净额（元）'
    """,
    """
    COMMENT ON COLUMN fund_flow_daily.mid_net_amount IS '中单净流入净额（元）'
    """,
    """
    COMMENT ON COLUMN fund_flow_daily.small_net_amount IS '小单净流入净额（元）'
    """,
]


DOWNGRADE_STATEMENTS = [
    "DROP INDEX IF EXISTS idx_fund_flow_ts_date",
    "DROP TABLE IF EXISTS fund_flow_daily",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
