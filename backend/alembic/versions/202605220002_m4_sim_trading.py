"""Add M4 signal and simulation trading tables.

Revision ID: 202605220002
Revises: 202605220001
Create Date: 2026-05-22 00:02:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605220002"
down_revision: str | None = "202605220001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE sim_accounts (
        id                BIGSERIAL PRIMARY KEY,
        user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        strategy_id        BIGINT REFERENCES strategies(id) ON DELETE SET NULL,
        name              VARCHAR(100) NOT NULL,
        initial_cash      NUMERIC(20,4) NOT NULL,
        available_cash    NUMERIC(20,4) NOT NULL,
        frozen_cash       NUMERIC(20,4) NOT NULL DEFAULT 0,
        total_asset       NUMERIC(20,4) NOT NULL,
        status            VARCHAR(20) NOT NULL DEFAULT 'active',
        config            JSONB NOT NULL DEFAULT '{}'::JSONB,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (status IN ('active', 'paused', 'closed')),
        CHECK (initial_cash >= 0),
        CHECK (available_cash >= 0),
        CHECK (frozen_cash >= 0),
        CHECK (total_asset >= 0)
    )
    """,
    "CREATE INDEX idx_sim_accounts_user_status ON sim_accounts(user_id, status)",
    """
    CREATE TABLE sim_positions (
        id                BIGSERIAL PRIMARY KEY,
        account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
        ts_code           VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        shares            INTEGER NOT NULL DEFAULT 0,
        available_shares  INTEGER NOT NULL DEFAULT 0,
        frozen_shares     INTEGER NOT NULL DEFAULT 0,
        avg_cost          NUMERIC(12,4) NOT NULL DEFAULT 0,
        current_price     NUMERIC(12,4),
        market_value      NUMERIC(20,4) NOT NULL DEFAULT 0,
        unrealized_pnl    NUMERIC(20,4) NOT NULL DEFAULT 0,
        profit_rate       NUMERIC(14,8) NOT NULL DEFAULT 0,
        first_buy_date    DATE,
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (account_id, ts_code),
        CHECK (shares >= 0),
        CHECK (available_shares >= 0),
        CHECK (frozen_shares >= 0)
    )
    """,
    "CREATE INDEX idx_sim_positions_account ON sim_positions(account_id)",
    """
    CREATE TABLE sim_orders (
        id                BIGSERIAL PRIMARY KEY,
        account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
        signal_id         BIGINT REFERENCES signal_log(id) ON DELETE SET NULL,
        ts_code           VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        direction         VARCHAR(4) NOT NULL,
        order_type        VARCHAR(10) NOT NULL DEFAULT '限价',
        price             NUMERIC(12,4),
        volume            INTEGER NOT NULL,
        filled_volume     INTEGER NOT NULL DEFAULT 0,
        frozen_amount     NUMERIC(20,4) NOT NULL DEFAULT 0,
        status            VARCHAR(20) NOT NULL DEFAULT '待成交',
        reject_reason     TEXT,
        submit_time       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        update_time       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        cancel_time       TIMESTAMPTZ,
        CHECK (direction IN ('买入', '卖出')),
        CHECK (order_type IN ('限价', '市价')),
        CHECK (status IN ('待成交', '部分成交', '全部成交', '已撤单', '已拒绝', '已过期')),
        CHECK (volume > 0),
        CHECK (filled_volume >= 0)
    )
    """,
    "CREATE INDEX idx_sim_orders_account_status ON sim_orders(account_id, status)",
    "CREATE INDEX idx_sim_orders_code_time ON sim_orders(ts_code, submit_time DESC)",
    """
    CREATE TABLE sim_trades (
        id                BIGSERIAL PRIMARY KEY,
        order_id          BIGINT NOT NULL REFERENCES sim_orders(id) ON DELETE CASCADE,
        account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
        ts_code           VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        direction         VARCHAR(4) NOT NULL,
        price             NUMERIC(12,4) NOT NULL,
        volume            INTEGER NOT NULL,
        amount            NUMERIC(20,4) NOT NULL,
        stamp_tax         NUMERIC(20,4) NOT NULL DEFAULT 0,
        commission        NUMERIC(20,4) NOT NULL DEFAULT 0,
        transfer_fee      NUMERIC(20,4) NOT NULL DEFAULT 0,
        total_fee         NUMERIC(20,4) NOT NULL DEFAULT 0,
        trade_time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (direction IN ('买入', '卖出')),
        CHECK (volume > 0)
    )
    """,
    "CREATE INDEX idx_sim_trades_account_time ON sim_trades(account_id, trade_time DESC)",
    "CREATE INDEX idx_sim_trades_order ON sim_trades(order_id)",
    """
    CREATE TABLE sim_cash_flow (
        id                BIGSERIAL PRIMARY KEY,
        account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
        related_trade_id  BIGINT REFERENCES sim_trades(id) ON DELETE SET NULL,
        flow_type         VARCHAR(20) NOT NULL,
        amount            NUMERIC(20,4) NOT NULL,
        balance_after     NUMERIC(20,4) NOT NULL,
        remark            TEXT,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (flow_type IN ('买入', '卖出', '手续费', '分红', '利息', '充值', '调整', '冻结', '解冻'))
    )
    """,
    "CREATE INDEX idx_cash_flow_account_time ON sim_cash_flow(account_id, created_at DESC)",
    """
    CREATE TABLE sim_daily_nav (
        id                BIGSERIAL PRIMARY KEY,
        account_id        BIGINT NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
        nav_date          DATE NOT NULL,
        total_asset       NUMERIC(20,4) NOT NULL,
        available_cash    NUMERIC(20,4) NOT NULL,
        frozen_cash       NUMERIC(20,4) NOT NULL DEFAULT 0,
        position_value    NUMERIC(20,4) NOT NULL DEFAULT 0,
        daily_return      NUMERIC(14,8) NOT NULL DEFAULT 0,
        cumulative_nav    NUMERIC(14,8) NOT NULL DEFAULT 1,
        max_drawdown      NUMERIC(14,8) NOT NULL DEFAULT 0,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (account_id, nav_date)
    )
    """,
    "CREATE INDEX idx_sim_daily_nav_account_date ON sim_daily_nav(account_id, nav_date DESC)",
    """
    ALTER TABLE signal_log
    ADD CONSTRAINT signal_log_account_id_fkey
    FOREIGN KEY (account_id) REFERENCES sim_accounts(id) ON DELETE SET NULL
    """,
    "CREATE INDEX idx_signal_account_date ON signal_log(account_id, trade_date DESC)",
]


DOWNGRADE_STATEMENTS = [
    "DROP INDEX IF EXISTS idx_signal_account_date",
    "ALTER TABLE signal_log DROP CONSTRAINT IF EXISTS signal_log_account_id_fkey",
    "DROP TABLE IF EXISTS sim_daily_nav",
    "DROP TABLE IF EXISTS sim_cash_flow",
    "DROP TABLE IF EXISTS sim_trades",
    "DROP TABLE IF EXISTS sim_orders",
    "DROP TABLE IF EXISTS sim_positions",
    "DROP TABLE IF EXISTS sim_accounts",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
