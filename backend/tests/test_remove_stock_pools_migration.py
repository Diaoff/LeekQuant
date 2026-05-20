from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_remove_pool_migration():
    path = Path("backend/alembic/versions/202605200004_remove_stock_pools.py")
    spec = importlib.util.spec_from_file_location("remove_pool_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remove_stock_pools_migration_drops_pool_tables_and_columns() -> None:
    migration = _load_remove_pool_migration()

    assert migration.down_revision == "202605200002"
    assert "ALTER TABLE strategies DROP COLUMN IF EXISTS pool_id" in migration.UPGRADE_STATEMENTS
    assert "ALTER TABLE backtest_results DROP COLUMN IF EXISTS pool_id" in migration.UPGRADE_STATEMENTS
    assert "DROP TABLE IF EXISTS stock_pools" in migration.UPGRADE_STATEMENTS
    assert "DROP TABLE IF EXISTS stock_pool_items" in migration.UPGRADE_STATEMENTS
