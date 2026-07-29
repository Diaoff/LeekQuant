"""Tests for the kline sync guarantee migration (revision 202607220001).

Two layers of tests:

* Structural assertions over the migration's ``UPGRADE_STATEMENTS`` /
  ``DOWNGRADE_STATEMENTS`` SQL text — these run always, with no DB
  dependency, mirroring the style of ``test_m4_sim_migration.py``.

* Functional assertions against a real PostgreSQL instance — these are
  gated on ``DATABASE_URL`` being resolvable. When the env is unset the
  tests are skipped, so ``pytest`` still passes in CI without a DB.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

# Resolve paths from this test file so the tests work whether pytest is
# launched from the project root or from backend/.
THIS_FILE = Path(__file__).resolve()
BACKEND_DIR = THIS_FILE.parents[1]
MIGRATION_PATH = BACKEND_DIR / "alembic" / "versions" / "202607220001_kline_sync_guarantee.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "kline_sync_guarantee_migration", MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_pg_dsn() -> str | None:
    """Return a plain ``postgresql://`` DSN if DATABASE_URL is configured.

    Returns None when Settings cannot be built (missing .env) so callers can
    skip the real-DB tests gracefully.
    """
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            from app.core.config import get_settings

            raw = get_settings().database_url
        except Exception:
            return None
    if not raw:
        return None
    return raw.replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# Structural assertions (no DB)
# ---------------------------------------------------------------------------


def test_migration_revision_chain():
    migration = _load_migration()
    assert migration.revision == "202607220001"
    assert migration.down_revision == "202607200001"


def test_upgrade_creates_kline_sync_failures_table_with_all_columns():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert "CREATE TABLE kline_sync_failures" in sql
    for column in [
        "id                     BIGSERIAL PRIMARY KEY",
        "parent_task_id         BIGINT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE",
        "batch_task_id          VARCHAR(128)",
        "ts_code                VARCHAR(16) NOT NULL",
        "failure_count          INTEGER NOT NULL DEFAULT 1",
        "last_error             TEXT",
        "last_failed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "is_permanent_failure   BOOLEAN NOT NULL DEFAULT FALSE",
        "UNIQUE (parent_task_id, ts_code)",
    ]:
        assert column in sql, f"missing column/clause: {column!r}"


def test_upgrade_creates_kline_sync_failures_index():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)
    assert (
        "CREATE INDEX idx_kline_sync_failures_ts_code_permanent "
        "ON kline_sync_failures (ts_code, is_permanent_failure)"
    ) in sql


def test_upgrade_deduplicates_task_runs_task_id_before_unique():
    migration = _load_migration()
    upgrade = migration.UPGRADE_STATEMENTS
    sql_joined = "\n".join(upgrade)

    # DELETE must come before the ADD CONSTRAINT.
    delete_idx = next(
        i for i, s in enumerate(upgrade) if "DELETE FROM task_runs a USING task_runs b" in s
    )
    add_idx = next(
        i
        for i, s in enumerate(upgrade)
        if "ALTER TABLE task_runs ADD CONSTRAINT uq_task_runs_task_id UNIQUE (task_id)" in s
    )
    assert delete_idx < add_idx

    assert "a.task_id = b.task_id" in sql_joined
    assert "a.id < b.id" in sql_joined
    assert "a.task_id IS NOT NULL" in sql_joined


def test_upgrade_drops_legacy_status_check_dynamically_then_re_adds_with_dispatched():
    migration = _load_migration()
    upgrade = migration.UPGRADE_STATEMENTS
    sql_joined = "\n".join(upgrade)

    # The DO block must query pg_constraint for the legacy (unnamed) check
    # and drop it dynamically.
    assert "pg_constraint" in sql_joined
    assert "pg_get_constraintdef(oid) LIKE '%status%'" in sql_joined
    assert "EXECUTE format('ALTER TABLE task_runs DROP CONSTRAINT %I', cname)" in sql_joined

    # The replacement CHECK must be named and include 'dispatched'.
    add_check = next(
        s for s in upgrade
        if "ADD CONSTRAINT task_runs_status_check" in s and "CHECK (status IN" in s
    )
    assert "'dispatched'" in add_check
    for legacy_status in ("'pending'", "'running'", "'success'", "'failed'", "'cancelled'"):
        assert legacy_status in add_check


def test_downgrade_reverts_all_three_changes():
    migration = _load_migration()
    downgrade_sql = "\n".join(migration.DOWNGRADE_STATEMENTS)

    # 1. Drop the new named status check and rebuild without 'dispatched'.
    assert "ALTER TABLE task_runs DROP CONSTRAINT IF EXISTS task_runs_status_check" in downgrade_sql
    rebuilt_check = next(
        s for s in migration.DOWNGRADE_STATEMENTS
        if "ADD CONSTRAINT task_runs_status_check" in s and "CHECK (status IN" in s
    )
    assert "'dispatched'" not in rebuilt_check
    for legacy_status in ("'pending'", "'running'", "'success'", "'failed'", "'cancelled'"):
        assert legacy_status in rebuilt_check

    # 2. Drop the UNIQUE constraint on task_id.
    assert "ALTER TABLE task_runs DROP CONSTRAINT IF EXISTS uq_task_runs_task_id" in downgrade_sql

    # 3. Drop the new table.
    assert "DROP TABLE IF EXISTS kline_sync_failures" in downgrade_sql


def test_downgrade_drops_table_after_unique_constraint():
    """DROP TABLE must come after the UNIQUE constraint drop so FK / index
    teardown on kline_sync_failures (which references task_runs) is clean
    — actually the table owns the FK, but ordering keeps the statements
    readable and matches the upgrade reverse order."""
    migration = _load_migration()
    downgrade = migration.DOWNGRADE_STATEMENTS
    unique_drop_idx = next(
        i for i, s in enumerate(downgrade)
        if "DROP CONSTRAINT IF EXISTS uq_task_runs_task_id" in s
    )
    table_drop_idx = next(
        i for i, s in enumerate(downgrade)
        if "DROP TABLE IF EXISTS kline_sync_failures" in s
    )
    assert unique_drop_idx < table_drop_idx


# ---------------------------------------------------------------------------
# Functional assertions (real PostgreSQL)
# ---------------------------------------------------------------------------

PG_DSN = _resolve_pg_dsn()
requires_real_db = pytest.mark.skipif(
    PG_DSN is None, reason="needs real DB (DATABASE_URL unset)"
)


@pytest.mark.asyncio
@requires_real_db
async def test_kline_sync_failures_table_exists_with_columns():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        row = await conn.fetchrow(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'kline_sync_failures'
            ORDER BY ordinal_position
            """
        )
        assert row is not None, "kline_sync_failures table missing"
        columns = {
            r["column_name"]: r for r in await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'kline_sync_failures'
                """
            )
        }
        assert set(columns) >= {
            "id", "parent_task_id", "batch_task_id", "ts_code",
            "failure_count", "last_error", "last_failed_at",
            "is_permanent_failure",
        }
        assert columns["ts_code"]["is_nullable"] == "NO"
        assert columns["parent_task_id"]["is_nullable"] == "NO"
        assert columns["failure_count"]["data_type"] == "integer"
        assert columns["is_permanent_failure"]["data_type"] == "boolean"
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_kline_sync_failures_unique_constraint_enforced():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        # Insert a parent task_run to satisfy the FK.
        parent_id = await conn.fetchval(
            """
            INSERT INTO task_runs (task_name, task_id, status)
            VALUES ('kline_sync_test_unique', 'parent-' || gen_random_uuid()::text, 'success')
            RETURNING id
            """
        )
        try:
            await conn.execute(
                """
                INSERT INTO kline_sync_failures (parent_task_id, ts_code, last_error)
                VALUES ($1, '000001.SZ', 'first')
                """,
                parent_id,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    """
                    INSERT INTO kline_sync_failures (parent_task_id, ts_code, last_error)
                    VALUES ($1, '000001.SZ', 'second')
                    """,
                    parent_id,
                )
        finally:
            # FK is ON DELETE CASCADE; deleting the parent removes failures.
            await conn.execute("DELETE FROM task_runs WHERE id = $1", parent_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_task_runs_task_id_unique_constraint_enforced():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        marker = "unique-test-" + __import__("uuid").uuid4().hex
        first_id = await conn.fetchval(
            """
            INSERT INTO task_runs (task_name, task_id, status)
            VALUES ('kline_sync_unique_task_id', $1, 'success')
            RETURNING id
            """,
            marker,
        )
        try:
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    """
                    INSERT INTO task_runs (task_name, task_id, status)
                    VALUES ('kline_sync_unique_task_id', $1, 'success')
                    """,
                    marker,
                )
        finally:
            await conn.execute("DELETE FROM task_runs WHERE id = $1", first_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_task_runs_status_accepts_dispatched():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        marker = "dispatched-test-" + __import__("uuid").uuid4().hex
        row_id = await conn.fetchval(
            """
            INSERT INTO task_runs (task_name, task_id, status)
            VALUES ('kline_sync_dispatched_status', $1, 'dispatched')
            RETURNING id
            """,
            marker,
        )
        try:
            stored = await conn.fetchval(
                "SELECT status FROM task_runs WHERE id = $1", row_id
            )
            assert stored == "dispatched"
        finally:
            await conn.execute("DELETE FROM task_runs WHERE id = $1", row_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_migration_downgrade_round_trip():
    """Run alembic downgrade -1 then upgrade head against the live DB.

    This exercises the DOWNGRADE_STATEMENTS (drop check / unique / table)
    and re-applies the upgrade. If any statement fails the test surfaces
    the error directly.
    """
    import subprocess

    result = subprocess.run(
        [str(BACKEND_DIR / ".venv" / "bin" / "python"), "-m", "alembic", "downgrade", "-1"],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic downgrade -1 failed:\n{result.stderr}\n{result.stdout}")

    try:
        result = subprocess.run(
            [str(BACKEND_DIR / ".venv" / "bin" / "python"), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.fail(f"alembic upgrade head (re-apply) failed:\n{result.stderr}\n{result.stdout}")
    finally:
        # Always restore head so subsequent tests see the upgraded state.
        subprocess.run(
            [str(BACKEND_DIR / ".venv" / "bin" / "python"), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
        )
