import importlib.util
from pathlib import Path


def load_migration():
    path = Path("backend/alembic/versions/202605220002_m4_sim_trading.py")
    spec = importlib.util.spec_from_file_location("m4_sim_migration", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)
    return migration


def test_m4_migration_creates_sim_trading_tables_and_signal_fk():
    migration = load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert migration.down_revision == "202605220001"
    for table in [
        "sim_accounts",
        "sim_positions",
        "sim_orders",
        "sim_trades",
        "sim_cash_flow",
        "sim_daily_nav",
    ]:
        assert f"CREATE TABLE {table}" in sql

    assert "FOREIGN KEY (account_id) REFERENCES sim_accounts(id)" in sql
    assert "UNIQUE (account_id, ts_code)" in sql
    assert "UNIQUE (account_id, nav_date)" in sql
    assert "CHECK (direction IN ('买入', '卖出'))" in sql
    assert "CHECK (status IN ('待成交', '部分成交', '全部成交', '已撤单', '已拒绝', '已过期'))" in sql


def test_m4_migration_downgrade_removes_tables_in_dependency_order():
    migration = load_migration()
    downgrade_sql = "\n".join(migration.DOWNGRADE_STATEMENTS)

    assert "ALTER TABLE signal_log DROP CONSTRAINT IF EXISTS signal_log_account_id_fkey" in downgrade_sql
    assert migration.DOWNGRADE_STATEMENTS.index("DROP TABLE IF EXISTS sim_trades") < migration.DOWNGRADE_STATEMENTS.index("DROP TABLE IF EXISTS sim_orders")
    assert migration.DOWNGRADE_STATEMENTS[-1] == "DROP TABLE IF EXISTS sim_accounts"
