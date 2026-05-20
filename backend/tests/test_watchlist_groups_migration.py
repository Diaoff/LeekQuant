from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_watchlist_groups_migration():
    path = Path("backend/alembic/versions/202605200005_add_watchlist_groups.py")
    spec = importlib.util.spec_from_file_location("watchlist_groups_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_watchlist_groups_migration_creates_persistent_group_table() -> None:
    migration = _load_watchlist_groups_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert migration.down_revision == "202605200004"
    assert "CREATE TABLE watchlist_groups" in sql
    assert "UNIQUE (user_id, group_name)" in sql
    assert "SELECT DISTINCT user_id, group_name" in sql
    assert "VALUES (1, '默认')" in sql
    assert "COMMENT ON TABLE watchlist_groups" in sql
