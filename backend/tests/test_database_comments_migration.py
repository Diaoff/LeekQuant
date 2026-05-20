from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_comments_migration():
    path = Path("backend/alembic/versions/202605200001_add_database_comments.py")
    spec = importlib.util.spec_from_file_location("comments_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_database_comments_migration_documents_operational_fields() -> None:
    migration = _load_comments_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert migration.down_revision == "202605180003"
    assert "COMMENT ON TABLE stock_basic IS 'A股股票基础信息表" in sql
    assert "COMMENT ON COLUMN stock_basic.market IS '市场板块" in sql
    assert "COMMENT ON COLUMN daily_kline.raw_payload IS '原始数据源返回内容" in sql
    assert "COMMENT ON COLUMN backtest_results.trade_records IS '交易明细 JSON 数组'" in sql
    assert "COMMENT ON COLUMN signal_log.signal_type IS '五档信号" in sql
    assert len(migration.DOWNGRADE_STATEMENTS) == len(migration.UPGRADE_STATEMENTS)
    assert all(statement.endswith(" IS NULL") for statement in migration.DOWNGRADE_STATEMENTS)
