"""Add M3 strategy, backtest and signal tables.

Revision ID: 202605180002
Revises: 202605180001
Create Date: 2026-05-18 00:02:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605180002"
down_revision: str | None = "202605180001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE strategies (
        id             BIGSERIAL PRIMARY KEY,
        user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        pool_id        BIGINT REFERENCES stock_pools(id) ON DELETE SET NULL,
        name           VARCHAR(100) NOT NULL,
        description    TEXT,
        source_code    TEXT NOT NULL,
        config         JSONB NOT NULL DEFAULT '{}'::JSONB,
        version        INTEGER NOT NULL DEFAULT 1,
        status         VARCHAR(20) NOT NULL DEFAULT 'draft',
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        archived_at    TIMESTAMPTZ,
        CHECK (status IN ('draft', 'active', 'paused', 'archived'))
    )
    """,
    "CREATE INDEX idx_strategies_user_status ON strategies(user_id, status)",
    """
    CREATE TABLE backtest_results (
        id               BIGSERIAL PRIMARY KEY,
        user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        strategy_id      BIGINT REFERENCES strategies(id) ON DELETE SET NULL,
        pool_id          BIGINT REFERENCES stock_pools(id) ON DELETE SET NULL,
        task_id          VARCHAR(128),
        start_date       DATE NOT NULL,
        end_date         DATE NOT NULL,
        initial_cash     NUMERIC(20,4) NOT NULL,
        benchmark_code   VARCHAR(16),
        params_snapshot  JSONB NOT NULL DEFAULT '{}'::JSONB,
        total_return     NUMERIC(14,8),
        annual_return    NUMERIC(14,8),
        sharpe_ratio     NUMERIC(14,8),
        max_drawdown     NUMERIC(14,8),
        annual_vol       NUMERIC(14,8),
        win_rate         NUMERIC(14,8),
        trade_count      INTEGER,
        performance      JSONB NOT NULL DEFAULT '{}'::JSONB,
        trade_records    JSONB NOT NULL DEFAULT '[]'::JSONB,
        equity_curve     JSONB NOT NULL DEFAULT '[]'::JSONB,
        status           VARCHAR(20) NOT NULL DEFAULT 'pending',
        error_message    TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at       TIMESTAMPTZ,
        finished_at      TIMESTAMPTZ,
        CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled'))
    )
    """,
    "CREATE INDEX idx_backtest_user_created ON backtest_results(user_id, created_at DESC)",
    "CREATE INDEX idx_backtest_strategy ON backtest_results(strategy_id, created_at DESC)",
    """
    CREATE TABLE signal_log (
        id               BIGSERIAL PRIMARY KEY,
        user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        strategy_id      BIGINT REFERENCES strategies(id) ON DELETE SET NULL,
        account_id       BIGINT,
        ts_code          VARCHAR(10) NOT NULL REFERENCES stock_basic(ts_code),
        trade_date       DATE NOT NULL,
        signal_type      VARCHAR(10) NOT NULL,
        target_position  NUMERIC(8,6) NOT NULL DEFAULT 0,
        current_position NUMERIC(8,6) NOT NULL DEFAULT 0,
        action           VARCHAR(32),
        confidence       NUMERIC(8,6),
        reason           TEXT,
        snapshot         JSONB NOT NULL DEFAULT '{}'::JSONB,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (signal_type IN ('买入', '增持', '减仓', '卖出', '观望'))
    )
    """,
    "CREATE INDEX idx_signal_user_date ON signal_log(user_id, trade_date DESC)",
    "CREATE INDEX idx_signal_code_date ON signal_log(ts_code, trade_date DESC)",
    "CREATE INDEX idx_signal_strategy_date ON signal_log(strategy_id, trade_date DESC)",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in [
        "DROP TABLE IF EXISTS signal_log",
        "DROP TABLE IF EXISTS backtest_results",
        "DROP TABLE IF EXISTS strategies",
    ]:
        op.execute(statement)
