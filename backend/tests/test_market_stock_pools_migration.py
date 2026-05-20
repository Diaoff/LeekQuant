from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_market_pool_migration():
    path = Path("backend/alembic/versions/202605200002_seed_market_stock_pools.py")
    spec = importlib.util.spec_from_file_location("market_pool_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_market_stock_pool_migration_seeds_board_options() -> None:
    migration = _load_market_pool_migration()

    assert migration.down_revision == "202605200001"
    names = [pool["name"] for pool in migration.SYSTEM_POOLS]
    assert names == ["主板", "创业板", "科创板", "北交所"]
    assert all('"exclude_st": true' in pool["filters"] for pool in migration.SYSTEM_POOLS)
    assert all('"exclude_delisted": true' in pool["filters"] for pool in migration.SYSTEM_POOLS)
    assert {pool["market"] for pool in migration.SYSTEM_POOLS} == {"主板", "创业板", "科创板", "北交所"}
    assert "UPDATE stock_basic" in migration.BACKFILL_STOCK_BASIC_MARKET_SQL
    assert "688%" in migration.BACKFILL_STOCK_BASIC_MARKET_SQL
    assert "300%" in migration.BACKFILL_STOCK_BASIC_MARKET_SQL
    assert "WHERE market IS NULL OR market = ''" in migration.BACKFILL_STOCK_BASIC_MARKET_SQL
