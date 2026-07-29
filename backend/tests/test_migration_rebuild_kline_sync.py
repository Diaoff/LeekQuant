"""Tests for the rebuild kline sync queue migration (revision rebuild_kline_sync_queue).

Two layers of tests:

* Structural assertions over the migration's ``UPGRADE_STATEMENTS`` /
  ``DOWNGRADE_STATEMENTS`` SQL text — these run always, with no DB
  dependency, mirroring the style of ``test_migration_kline_sync_guarantee.py``.

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
MIGRATION_PATH = (
    BACKEND_DIR / "alembic" / "versions" / "202607230001_rebuild_kline_sync_queue.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "rebuild_kline_sync_queue_migration", MIGRATION_PATH
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
    assert migration.revision == "rebuild_kline_sync_queue"
    assert migration.down_revision == "202607220001"


def test_upgrade_creates_kline_sync_jobs_table_with_all_columns():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert "CREATE TABLE kline_sync_jobs" in sql
    for column in [
        "id                      BIGSERIAL PRIMARY KEY",
        "job_type                VARCHAR(32) NOT NULL",
        "status                  VARCHAR(20) NOT NULL DEFAULT 'running'",
        "scope_total             INTEGER NOT NULL DEFAULT 0",
        "scope_done              INTEGER NOT NULL DEFAULT 0",
        "scope_failed            INTEGER NOT NULL DEFAULT 0",
        "permanent_failure_codes TEXT[] NOT NULL DEFAULT '{}'",
        "config                  JSONB NOT NULL DEFAULT '{}'::jsonb",
        "created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "started_at              TIMESTAMPTZ",
        "completed_at            TIMESTAMPTZ",
        "error                   TEXT",
    ]:
        assert column in sql, f"missing column/clause: {column!r}"


def test_upgrade_creates_kline_sync_jobs_check_constraints():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert (
        "CONSTRAINT kline_sync_jobs_status_check "
        "CHECK (status IN ('running', 'completed', 'failed'))"
    ) in sql
    assert (
        "CONSTRAINT kline_sync_jobs_type_check "
        "CHECK (job_type IN ('incremental', 'full'))"
    ) in sql


def test_upgrade_creates_kline_sync_jobs_indexes():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert (
        "CREATE INDEX idx_kline_sync_jobs_created_at ON kline_sync_jobs (created_at DESC)"
    ) in sql
    assert "CREATE INDEX idx_kline_sync_jobs_status ON kline_sync_jobs (status)" in sql


def test_upgrade_creates_kline_sync_items_table_with_all_columns():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert "CREATE TABLE kline_sync_items" in sql
    for column in [
        "id              BIGSERIAL PRIMARY KEY",
        "job_id          BIGINT NOT NULL REFERENCES kline_sync_jobs(id) ON DELETE CASCADE",
        "ts_code         VARCHAR(16) NOT NULL",
        "start_date      DATE NOT NULL",
        "end_date        DATE NOT NULL",
        "status          VARCHAR(20) NOT NULL DEFAULT 'pending'",
        "attempts        INTEGER NOT NULL DEFAULT 0",
        "last_error      TEXT",
        "last_attempt_at TIMESTAMPTZ",
        "worker_id       VARCHAR(64)",
    ]:
        assert column in sql, f"missing column/clause: {column!r}"


def test_upgrade_creates_kline_sync_items_check_constraint():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert (
        "CONSTRAINT kline_sync_items_status_check "
        "CHECK (status IN ('pending', 'running', 'done', 'permanently_failed'))"
    ) in sql


def test_upgrade_creates_kline_sync_items_unique_constraint():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert (
        "CONSTRAINT kline_sync_items_unique "
        "UNIQUE (job_id, ts_code, start_date, end_date)"
    ) in sql


def test_upgrade_creates_kline_sync_items_indexes():
    migration = _load_migration()
    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert (
        "CREATE INDEX idx_kline_sync_items_job_status ON kline_sync_items (job_id, status)"
    ) in sql
    assert (
        "CREATE INDEX idx_kline_sync_items_stuck "
        "ON kline_sync_items (last_attempt_at) WHERE status = 'running'"
    ) in sql


def test_upgrade_creates_jobs_before_items():
    """kline_sync_items references kline_sync_jobs via FK, so the jobs
    table must be created first."""
    migration = _load_migration()
    upgrade = migration.UPGRADE_STATEMENTS
    jobs_idx = next(
        i for i, s in enumerate(upgrade) if "CREATE TABLE kline_sync_jobs" in s
    )
    items_idx = next(
        i for i, s in enumerate(upgrade) if "CREATE TABLE kline_sync_items" in s
    )
    assert jobs_idx < items_idx


def test_downgrade_drops_items_before_jobs():
    """kline_sync_items holds the FK to kline_sync_jobs, so it must be
    dropped first to avoid dangling references."""
    migration = _load_migration()
    downgrade = migration.DOWNGRADE_STATEMENTS
    items_idx = next(
        i for i, s in enumerate(downgrade) if "DROP TABLE IF EXISTS kline_sync_items" in s
    )
    jobs_idx = next(
        i for i, s in enumerate(downgrade) if "DROP TABLE IF EXISTS kline_sync_jobs" in s
    )
    assert items_idx < jobs_idx


def test_downgrade_drops_both_tables():
    migration = _load_migration()
    downgrade_sql = "\n".join(migration.DOWNGRADE_STATEMENTS)

    assert "DROP TABLE IF EXISTS kline_sync_items" in downgrade_sql
    assert "DROP TABLE IF EXISTS kline_sync_jobs" in downgrade_sql


# ---------------------------------------------------------------------------
# Functional assertions (real PostgreSQL)
# ---------------------------------------------------------------------------

PG_DSN = _resolve_pg_dsn()
requires_real_db = pytest.mark.skipif(
    PG_DSN is None, reason="needs real DB (DATABASE_URL unset)"
)


@pytest.mark.asyncio
@requires_real_db
async def test_both_tables_exist_after_upgrade():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        for table in ("kline_sync_jobs", "kline_sync_items"):
            exists = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", table
            )
            assert exists, f"table {table} missing after upgrade"
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_kline_sync_jobs_columns_and_defaults():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        columns = {
            r["column_name"]: r
            for r in await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = 'kline_sync_jobs'
                """
            )
        }
        expected = {
            "id",
            "job_type",
            "status",
            "scope_total",
            "scope_done",
            "scope_failed",
            "permanent_failure_codes",
            "config",
            "created_at",
            "started_at",
            "completed_at",
            "error",
        }
        assert set(columns) >= expected

        assert columns["job_type"]["is_nullable"] == "NO"
        assert columns["status"]["is_nullable"] == "NO"
        assert columns["status"]["column_default"] == "'running'::character varying"
        assert columns["scope_total"]["data_type"] == "integer"
        assert columns["scope_total"]["column_default"] == "0"
        assert columns["permanent_failure_codes"]["data_type"] == "ARRAY"
        assert columns["config"]["data_type"] == "jsonb"
        assert columns["created_at"]["is_nullable"] == "NO"
        assert columns["error"]["is_nullable"] == "YES"
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_kline_sync_jobs_check_constraints_enforced():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        # job_type CHECK rejects unknown values.
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO kline_sync_jobs (job_type) VALUES ('bogus')"
            )

        # status CHECK rejects unknown values.
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO kline_sync_jobs (job_type, status) VALUES ('incremental', 'bogus')"
            )

        # Valid insert works.
        job_id = await conn.fetchval(
            "INSERT INTO kline_sync_jobs (job_type, status) VALUES ('full', 'running') RETURNING id"
        )
        await conn.execute("DELETE FROM kline_sync_jobs WHERE id = $1", job_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_kline_sync_items_columns_and_constraints():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        columns = {
            r["column_name"]: r
            for r in await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = 'kline_sync_items'
                """
            )
        }
        expected = {
            "id",
            "job_id",
            "ts_code",
            "start_date",
            "end_date",
            "status",
            "attempts",
            "last_error",
            "last_attempt_at",
            "worker_id",
        }
        assert set(columns) >= expected

        assert columns["job_id"]["is_nullable"] == "NO"
        assert columns["ts_code"]["is_nullable"] == "NO"
        assert columns["start_date"]["data_type"] == "date"
        assert columns["end_date"]["data_type"] == "date"
        assert columns["status"]["column_default"] == "'pending'::character varying"
        assert columns["attempts"]["column_default"] == "0"
        assert columns["worker_id"]["is_nullable"] == "YES"
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_kline_sync_items_status_check_enforced():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        job_id = await conn.fetchval(
            "INSERT INTO kline_sync_jobs (job_type) VALUES ('incremental') RETURNING id"
        )
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO kline_sync_items (job_id, ts_code, start_date, end_date, status)
                    VALUES ($1, '000001.SZ', '2020-01-01', '2020-12-31', 'bogus')
                    """,
                    job_id,
                )

            # Valid statuses all insert. Use a unique ts_code per status
            # (status[0] would collide for 'pending' and 'permanently_failed').
            for idx, status in enumerate(("pending", "running", "done", "permanently_failed")):
                await conn.execute(
                    """
                    INSERT INTO kline_sync_items (job_id, ts_code, start_date, end_date, status)
                    VALUES ($1, $2, '2020-01-01', '2020-12-31', $3)
                    """,
                    job_id,
                    f"00000{idx}.SZ",
                    status,
                )
        finally:
            await conn.execute("DELETE FROM kline_sync_jobs WHERE id = $1", job_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_kline_sync_items_unique_constraint_enforced():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        job_id = await conn.fetchval(
            "INSERT INTO kline_sync_jobs (job_type) VALUES ('incremental') RETURNING id"
        )
        try:
            await conn.execute(
                """
                INSERT INTO kline_sync_items (job_id, ts_code, start_date, end_date)
                VALUES ($1, '600000.SH', '2020-01-01', '2020-12-31')
                """,
                job_id,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    """
                    INSERT INTO kline_sync_items (job_id, ts_code, start_date, end_date)
                    VALUES ($1, '600000.SH', '2020-01-01', '2020-12-31')
                    """,
                    job_id,
                )
        finally:
            await conn.execute("DELETE FROM kline_sync_jobs WHERE id = $1", job_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_kline_sync_items_indexes_exist():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        indexes = {
            r["indexname"]
            for r in await conn.fetch(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE tablename IN ('kline_sync_jobs', 'kline_sync_items')
                """
            )
        }
        assert "idx_kline_sync_jobs_created_at" in indexes
        assert "idx_kline_sync_jobs_status" in indexes
        assert "idx_kline_sync_items_job_status" in indexes
        assert "idx_kline_sync_items_stuck" in indexes

        # The stuck index is a partial index scoped to status = 'running'.
        stuck_def = await conn.fetchval(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE tablename = 'kline_sync_items'
              AND indexname = 'idx_kline_sync_items_stuck'
            """
        )
        assert stuck_def is not None
        # PostgreSQL normalizes the partial-index predicate; accept either the
        # raw form or the normalized cast form.
        assert "status = 'running'" in stuck_def or "status)::text = 'running'::text" in stuck_def
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_fk_cascade_deletes_items_with_job():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    try:
        job_id = await conn.fetchval(
            "INSERT INTO kline_sync_jobs (job_type) VALUES ('incremental') RETURNING id"
        )
        await conn.execute(
            """
            INSERT INTO kline_sync_items (job_id, ts_code, start_date, end_date)
            VALUES ($1, '000001.SZ', '2020-01-01', '2020-12-31')
            """,
            job_id,
        )
        await conn.execute(
            """
            INSERT INTO kline_sync_items (job_id, ts_code, start_date, end_date)
            VALUES ($1, '600000.SH', '2021-01-01', '2021-12-31')
            """,
            job_id,
        )

        remaining = await conn.fetchval(
            "SELECT count(*) FROM kline_sync_items WHERE job_id = $1", job_id
        )
        assert remaining == 2

        # Deleting the job must cascade to both items.
        await conn.execute("DELETE FROM kline_sync_jobs WHERE id = $1", job_id)

        remaining_after = await conn.fetchval(
            "SELECT count(*) FROM kline_sync_items WHERE job_id = $1", job_id
        )
        assert remaining_after == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
@requires_real_db
async def test_migration_downgrade_round_trip():
    """Run alembic downgrade -1 then upgrade head against the live DB.

    This exercises the DOWNGRADE_STATEMENTS (drop both tables) and re-applies
    the upgrade. If any statement fails the test surfaces the error directly.
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
