import importlib.util
from pathlib import Path


def load_migration():
    path = Path("backend/alembic/versions/202605220003_m5_factors.py")
    spec = importlib.util.spec_from_file_location("m5_factor_migration", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)
    return migration


def test_m5_migration_creates_factor_tables_without_stock_pool_reference():
    migration = load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert migration.down_revision == "202605220002"
    for table in ["factor_definitions", "factor_values", "scoring_rank", "factor_analysis"]:
        assert f"CREATE TABLE {table}" in sql

    assert "pool_id" not in sql
    assert "stock_pools" not in sql
    assert "scope_type" in sql
    assert "scope_value" in sql
    assert "COALESCE(scope_value, '')" in sql
    assert "CHECK (scope_type IN ('all', 'watchlist_group'))" in sql


def test_m5_migration_seeds_builtin_factors_idempotently():
    migration = load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    for factor_name in ["pe_ttm", "pb", "roe", "revenue_growth", "mom_20d", "mom_60d", "rsi6", "vol_20d"]:
        assert factor_name in sql
    assert "ON CONFLICT (name) DO NOTHING" in sql
    assert "default_weight = EXCLUDED.default_weight" not in sql


def test_m5_migration_downgrade_removes_factor_tables_in_dependency_order():
    migration = load_migration()

    assert migration.DOWNGRADE_STATEMENTS.index("DROP TABLE IF EXISTS factor_analysis") < migration.DOWNGRADE_STATEMENTS.index("DROP TABLE IF EXISTS factor_definitions")
    assert migration.DOWNGRADE_STATEMENTS.index("DROP TABLE IF EXISTS factor_values") < migration.DOWNGRADE_STATEMENTS.index("DROP TABLE IF EXISTS factor_definitions")
    assert migration.DOWNGRADE_STATEMENTS[-1] == "DROP TABLE IF EXISTS factor_definitions"
