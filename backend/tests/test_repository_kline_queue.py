"""Tests for kline_sync_jobs / kline_sync_items repository functions.

Functional tests against a real PostgreSQL instance, gated on ``DATABASE_URL``.
When the env is unset the tests are skipped, so ``pytest`` still passes in CI
without a DB — mirroring the skip pattern in ``test_repository_kline_failures.py``.

Each test creates its own ``kline_sync_jobs`` row and cleans up via
``ON DELETE CASCADE`` so no leftover ``kline_sync_items`` rows leak between tests.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.data.repository import (
    claim_kline_sync_items,
    complete_job_if_done,
    create_kline_sync_job,
    get_job_progress,
    insert_kline_sync_items,
    list_job_items,
    list_recent_jobs,
    mark_item_done,
    mark_item_failed,
    recover_stuck_items,
    reset_failed_items_for_retry,
)


def _resolve_async_dsn() -> str | None:
    """Return the asyncpg DSN if DATABASE_URL is configured, else None."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            from app.core.config import get_settings

            raw = get_settings().database_url
        except Exception:
            return None
    if not raw:
        return None
    return raw


ASYNC_DSN = _resolve_async_dsn()
requires_real_db = pytest.mark.skipif(
    ASYNC_DSN is None, reason="needs real DB (DATABASE_URL unset)"
)


@asynccontextmanager
async def _temp_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a fresh AsyncSession with its own engine (disposed on exit).

    Each call creates a dedicated engine so the asyncpg connection is bound to
    the current event loop — pytest-asyncio strict mode runs every test in its
    own loop, so a module-level shared engine would leak connections across
    loops ("Future attached to a different loop").
    """
    engine = create_async_engine(ASYNC_DSN, pool_pre_ping=True)
    try:
        async with async_sessionmaker(bind=engine, expire_on_commit=False)() as s:
            yield s
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session():
    """Yield an AsyncSession bound to the real test DB."""
    async with _temp_session() as s:
        yield s


@pytest_asyncio.fixture
async def job_id(session: AsyncSession):
    """Insert a kline_sync_jobs row and clean it up (cascades to items)."""
    jid = await create_kline_sync_job(
        session, job_type="incremental", config={"test": True}
    )
    yield jid
    await session.execute(text("DELETE FROM kline_sync_jobs WHERE id = :id"), {"id": jid})
    await session.commit()


async def _insert_items(
    session: AsyncSession,
    *,
    job_id: int,
    codes: list[str],
    start: date = date(2026, 1, 1),
    end: date = date(2026, 1, 31),
) -> list[int]:
    """Insert items for the given ts_codes, return their ids ordered by id."""
    items = [{"ts_code": c, "start_date": start, "end_date": end} for c in codes]
    await insert_kline_sync_items(session, job_id=job_id, items=items)
    result = await session.execute(
        text("SELECT id FROM kline_sync_items WHERE job_id = :jid ORDER BY id"),
        {"jid": job_id},
    )
    return [int(r[0]) for r in result.fetchall()]


# ---------------------------------------------------------------------------
# create_kline_sync_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_create_job(session):
    jid = None
    try:
        jid = await create_kline_sync_job(
            session, job_type="full", config={"mode": "backfill"}
        )
        assert jid > 0
        row = (
            await session.execute(
                text("SELECT status, job_type, config FROM kline_sync_jobs WHERE id = :id"),
                {"id": jid},
            )
        ).mappings().one()
        assert row["status"] == "running"
        assert row["job_type"] == "full"
        assert row["config"]["mode"] == "backfill"
    finally:
        if jid is not None:
            await session.execute(text("DELETE FROM kline_sync_jobs WHERE id = :id"), {"id": jid})
            await session.commit()


# ---------------------------------------------------------------------------
# insert_kline_sync_items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_insert_items(session, job_id):
    codes = [f"00000{i}.SZ" for i in range(5)]
    count = await insert_kline_sync_items(
        session,
        job_id=job_id,
        items=[
            {"ts_code": c, "start_date": date(2026, 1, 1), "end_date": date(2026, 1, 31)}
            for c in codes
        ],
    )
    assert count == 5
    result = await session.execute(
        text("SELECT COUNT(*) FROM kline_sync_items WHERE job_id = :jid"),
        {"jid": job_id},
    )
    assert int(result.scalar_one()) == 5


# ---------------------------------------------------------------------------
# claim_kline_sync_items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_claim_items(session, job_id):
    ids = await _insert_items(
        session, job_id=job_id, codes=["000001.SZ", "000002.SZ", "000003.SZ"]
    )
    claimed = await claim_kline_sync_items(
        session, job_id=job_id, count=2, worker_id="w-1"
    )
    assert len(claimed) == 2
    claimed_ids = {c["id"] for c in claimed}
    assert claimed_ids.issubset(set(ids))
    # Verify claimed items are marked running with the right worker
    result = await session.execute(
        text(
            "SELECT status, worker_id FROM kline_sync_items "
            "WHERE job_id = :jid AND status = 'running'"
        ),
        {"jid": job_id},
    )
    rows = result.mappings().all()
    assert len(rows) == 2
    for row in rows:
        assert row["worker_id"] == "w-1"
    # The third item should still be pending
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM kline_sync_items "
            "WHERE job_id = :jid AND status = 'pending'"
        ),
        {"jid": job_id},
    )
    assert int(result.scalar_one()) == 1


@pytest.mark.asyncio
@requires_real_db
async def test_claim_atomic_no_duplicates(session, job_id):
    """Two workers claiming concurrently must not get overlapping items."""
    codes = [f"00000{i}.SZ" for i in range(5)]
    await _insert_items(session, job_id=job_id, codes=codes)

    async def _claim(worker: str, n: int):
        async with _temp_session() as s:
            return await claim_kline_sync_items(
                s, job_id=job_id, count=n, worker_id=worker
            )

    results = await asyncio.gather(_claim("w-1", 3), _claim("w-2", 2))
    claimed_1, claimed_2 = results
    assert len(claimed_1) == 3
    assert len(claimed_2) == 2

    ids_1 = {c["id"] for c in claimed_1}
    ids_2 = {c["id"] for c in claimed_2}
    assert ids_1.isdisjoint(ids_2)

    # All 5 items should now be running
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM kline_sync_items "
            "WHERE job_id = :jid AND status = 'running'"
        ),
        {"jid": job_id},
    )
    assert int(result.scalar_one()) == 5


# ---------------------------------------------------------------------------
# mark_item_done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_mark_item_done(session, job_id):
    ids = await _insert_items(session, job_id=job_id, codes=["000001.SZ"])
    item_id = ids[0]
    # Claim first so the item is in 'running' state
    await claim_kline_sync_items(session, job_id=job_id, count=1, worker_id="w-1")
    await mark_item_done(session, item_id=item_id, job_id=job_id)

    row = (
        await session.execute(
            text("SELECT status, worker_id FROM kline_sync_items WHERE id = :id"),
            {"id": item_id},
        )
    ).mappings().one()
    assert row["status"] == "done"
    assert row["worker_id"] is None

    jrow = (
        await session.execute(
            text("SELECT scope_done FROM kline_sync_jobs WHERE id = :id"),
            {"id": job_id},
        )
    ).mappings().one()
    assert jrow["scope_done"] == 1


# ---------------------------------------------------------------------------
# mark_item_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_mark_item_failed_retry(session, job_id):
    ids = await _insert_items(session, job_id=job_id, codes=["000001.SZ"])
    item_id = ids[0]
    # claim_kline_sync_items increments attempts (one try); mark_item_failed must NOT
    # increment again, otherwise a single failure would count as +2.
    await claim_kline_sync_items(session, job_id=job_id, count=1, worker_id="w1")
    is_perm = await mark_item_failed(
        session, item_id=item_id, job_id=job_id, error="boom", max_attempts=5
    )
    assert is_perm is False

    row = (
        await session.execute(
            text("SELECT status, attempts, last_error FROM kline_sync_items WHERE id = :id"),
            {"id": item_id},
        )
    ).mappings().one()
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["last_error"] == "boom"


@pytest.mark.asyncio
@requires_real_db
async def test_mark_item_failed_permanent(session, job_id):
    ids = await _insert_items(session, job_id=job_id, codes=["000001.SZ"])
    item_id = ids[0]
    # Claim first so attempts becomes 1, then fail with max_attempts=1 → permanent.
    await claim_kline_sync_items(session, job_id=job_id, count=1, worker_id="w1")
    is_perm = await mark_item_failed(
        session, item_id=item_id, job_id=job_id, error="fatal", max_attempts=1
    )
    assert is_perm is True

    row = (
        await session.execute(
            text("SELECT status, attempts FROM kline_sync_items WHERE id = :id"),
            {"id": item_id},
        )
    ).mappings().one()
    assert row["status"] == "permanently_failed"
    assert row["attempts"] == 1

    jrow = (
        await session.execute(
            text(
                "SELECT scope_failed, permanent_failure_codes "
                "FROM kline_sync_jobs WHERE id = :id"
            ),
            {"id": job_id},
        )
    ).mappings().one()
    assert jrow["scope_failed"] == 1
    assert "000001.SZ" in jrow["permanent_failure_codes"]


# ---------------------------------------------------------------------------
# recover_stuck_items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_recover_stuck(session, job_id):
    ids = await _insert_items(
        session, job_id=job_id, codes=["000001.SZ", "000002.SZ"]
    )
    # item 0: stuck (running with old last_attempt_at)
    await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = 'running', worker_id = 'w-1',
                last_attempt_at = NOW() - INTERVAL '2 hours'
            WHERE id = :id
            """
        ),
        {"id": ids[0]},
    )
    # item 1: running but recent (not stuck)
    await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = 'running', worker_id = 'w-2', last_attempt_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": ids[1]},
    )
    await session.commit()

    reset_count = await recover_stuck_items(session, stuck_seconds=3600)
    assert reset_count == 1

    row = (
        await session.execute(
            text("SELECT status, worker_id FROM kline_sync_items WHERE id = :id"),
            {"id": ids[0]},
        )
    ).mappings().one()
    assert row["status"] == "pending"
    assert row["worker_id"] is None

    row = (
        await session.execute(
            text("SELECT status FROM kline_sync_items WHERE id = :id"),
            {"id": ids[1]},
        )
    ).mappings().one()
    assert row["status"] == "running"


# ---------------------------------------------------------------------------
# complete_job_if_done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_complete_job_if_done(session, job_id):
    await _insert_items(session, job_id=job_id, codes=["000001.SZ", "000002.SZ"])
    # Mark all items as done directly
    await session.execute(
        text("UPDATE kline_sync_items SET status = 'done' WHERE job_id = :jid"),
        {"jid": job_id},
    )
    await session.commit()

    completed = await complete_job_if_done(session, job_id=job_id)
    assert completed is True

    row = (
        await session.execute(
            text("SELECT status, completed_at FROM kline_sync_jobs WHERE id = :id"),
            {"id": job_id},
        )
    ).mappings().one()
    assert row["status"] == "completed"
    assert row["completed_at"] is not None


@pytest.mark.asyncio
@requires_real_db
async def test_complete_job_not_done(session, job_id):
    ids = await _insert_items(
        session, job_id=job_id, codes=["000001.SZ", "000002.SZ"]
    )
    # Mark one done, leave the other pending
    await session.execute(
        text("UPDATE kline_sync_items SET status = 'done' WHERE id = :id"),
        {"id": ids[0]},
    )
    await session.commit()

    completed = await complete_job_if_done(session, job_id=job_id)
    assert completed is False

    row = (
        await session.execute(
            text("SELECT status FROM kline_sync_jobs WHERE id = :id"),
            {"id": job_id},
        )
    ).mappings().one()
    assert row["status"] == "running"


# ---------------------------------------------------------------------------
# get_job_progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_get_job_progress(session, job_id):
    ids = await _insert_items(
        session,
        job_id=job_id,
        codes=["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
    )
    # Set various statuses directly
    await session.execute(
        text("UPDATE kline_sync_items SET status = 'done' WHERE id = :id"),
        {"id": ids[0]},
    )
    await session.execute(
        text("UPDATE kline_sync_items SET status = 'running' WHERE id = :id"),
        {"id": ids[1]},
    )
    await session.execute(
        text("UPDATE kline_sync_items SET status = 'permanently_failed' WHERE id = :id"),
        {"id": ids[2]},
    )
    # ids[3] stays pending
    await session.commit()

    progress = await get_job_progress(session, job_id=job_id)
    assert progress["scope_total"] == 4
    assert progress["pending"] == 1
    assert progress["running"] == 1
    assert progress["done"] == 1
    assert progress["permanently_failed"] == 1
    assert progress["status"] == "running"


# ---------------------------------------------------------------------------
# reset_failed_items_for_retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_reset_failed_for_retry(session, job_id):
    await _insert_items(
        session, job_id=job_id, codes=["000001.SZ", "000002.SZ"]
    )
    # Mark both items as permanently_failed with attempts = 3
    await session.execute(
        text(
            "UPDATE kline_sync_items SET status = 'permanently_failed', attempts = 3 "
            "WHERE job_id = :jid"
        ),
        {"jid": job_id},
    )
    # Set job scope_failed and permanent_failure_codes
    await session.execute(
        text(
            "UPDATE kline_sync_jobs SET scope_failed = 2, "
            "permanent_failure_codes = ARRAY['000001.SZ', '000002.SZ'] WHERE id = :id"
        ),
        {"id": job_id},
    )
    await session.commit()

    reset_count = await reset_failed_items_for_retry(session, job_id=job_id)
    assert reset_count == 2

    result = await session.execute(
        text("SELECT status, attempts FROM kline_sync_items WHERE job_id = :jid"),
        {"jid": job_id},
    )
    for row in result.mappings().all():
        assert row["status"] == "pending"
        assert row["attempts"] == 0

    jrow = (
        await session.execute(
            text(
                "SELECT scope_failed, permanent_failure_codes "
                "FROM kline_sync_jobs WHERE id = :id"
            ),
            {"id": job_id},
        )
    ).mappings().one()
    assert jrow["scope_failed"] == 0
    assert jrow["permanent_failure_codes"] == []
