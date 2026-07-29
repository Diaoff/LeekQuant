"""Tests for kline_sync_failures repository CRUD functions.

Functional tests against a real PostgreSQL instance, gated on ``DATABASE_URL``.
When the env is unset the tests are skipped, so ``pytest`` still passes in CI
without a DB — mirroring the skip pattern in ``test_migration_kline_sync_guarantee.py``.

Each test inserts its own parent ``task_runs`` row (the FK target) and cleans
up via ``ON DELETE CASCADE`` so no leftover ``kline_sync_failures`` rows leak
between tests.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.data.repository import (
    append_kline_sync_failure,
    count_permanent_failures,
    list_kline_sync_failures,
    reset_dispatch_failed_batches,
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
async def parent_task_id(session: AsyncSession):
    """Insert a parent task_runs row and clean it up (cascades to failures)."""
    task_id = f"test-parent-{uuid.uuid4().hex}"
    result = await session.execute(
        text(
            """
            INSERT INTO task_runs (task_name, task_id, status)
            VALUES ('kline_sync_test_parent', :task_id, 'success')
            RETURNING id
            """
        ),
        {"task_id": task_id},
    )
    pid = int(result.scalar_one())
    await session.commit()
    yield pid
    await session.execute(text("DELETE FROM task_runs WHERE id = :id"), {"id": pid})
    await session.commit()


async def _insert_batch_row(
    session: AsyncSession,
    *,
    task_id: str,
    status: str = "failed",
    payload: dict | None = None,
) -> int:
    """Insert a task_runs batch row and return its id (cleanup is caller's job)."""
    import json

    result = await session.execute(
        text(
            """
            INSERT INTO task_runs (task_name, task_id, status, payload)
            VALUES ('kline_sync_test_batch', :task_id, :status, CAST(:payload AS JSONB))
            RETURNING id
            """
        ),
        {
            "task_id": task_id,
            "status": status,
            "payload": json.dumps(payload or {}),
        },
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# append_kline_sync_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_append_kline_sync_failure_inserts_new_record(session, parent_task_id):
    became = await append_kline_sync_failure(
        session,
        parent_task_id=parent_task_id,
        ts_code="000001.SZ",
        error="boom",
        batch_task_id="batch-1",
    )
    assert became is False

    rows = await list_kline_sync_failures(session, parent_task_id=parent_task_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["ts_code"] == "000001.SZ"
    assert row["failure_count"] == 1
    assert row["is_permanent_failure"] is False
    assert row["last_error"] == "boom"
    assert row["batch_task_id"] == "batch-1"


@pytest.mark.asyncio
@requires_real_db
async def test_append_kline_sync_failure_increments_on_conflict(session, parent_task_id):
    await append_kline_sync_failure(session, parent_task_id=parent_task_id, ts_code="600000.SH", error="e1")
    became2 = await append_kline_sync_failure(
        session, parent_task_id=parent_task_id, ts_code="600000.SH", error="e2"
    )
    await append_kline_sync_failure(session, parent_task_id=parent_task_id, ts_code="600000.SH", error="e3")

    assert became2 is False  # well below default threshold of 50

    rows = await list_kline_sync_failures(session, parent_task_id=parent_task_id)
    assert len(rows) == 1
    assert rows[0]["failure_count"] == 3
    assert rows[0]["last_error"] == "e3"  # last writer wins for the error text


@pytest.mark.asyncio
@requires_real_db
async def test_append_kline_sync_failure_marks_permanent_at_threshold(session, parent_task_id):
    threshold = 3
    results = []
    for i in range(4):
        became = await append_kline_sync_failure(
            session,
            parent_task_id=parent_task_id,
            ts_code="300001.SZ",
            error=f"err-{i}",
            permanent_failure_threshold=threshold,
        )
        results.append(became)

    # 1st (count=1) -> False, 2nd (count=2) -> False, 3rd (count=3) -> True, 4th (count=4) -> False
    assert results == [False, False, True, False]

    rows = await list_kline_sync_failures(session, parent_task_id=parent_task_id)
    assert rows[0]["failure_count"] == 4
    assert rows[0]["is_permanent_failure"] is True


# ---------------------------------------------------------------------------
# list_kline_sync_failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_list_kline_sync_failures_filters_by_parent(session):
    # Two distinct parents so we can verify the filter scopes correctly.
    pids = []
    for _ in range(2):
        tid = f"test-parent-{uuid.uuid4().hex}"
        r = await session.execute(
            text(
                "INSERT INTO task_runs (task_name, task_id, status) "
                "VALUES ('kline_sync_test_parent', :t, 'success') RETURNING id"
            ),
            {"t": tid},
        )
        pids.append(int(r.scalar_one()))
    await session.commit()
    try:
        await append_kline_sync_failure(session, parent_task_id=pids[0], ts_code="000001.SZ", error="a")
        await append_kline_sync_failure(session, parent_task_id=pids[0], ts_code="000002.SZ", error="b")
        await append_kline_sync_failure(session, parent_task_id=pids[1], ts_code="600000.SH", error="c")

        rows_p0 = await list_kline_sync_failures(session, parent_task_id=pids[0])
        rows_p1 = await list_kline_sync_failures(session, parent_task_id=pids[1])
        assert {r["ts_code"] for r in rows_p0} == {"000001.SZ", "000002.SZ"}
        assert {r["ts_code"] for r in rows_p1} == {"600000.SH"}
    finally:
        for pid in pids:
            await session.execute(text("DELETE FROM task_runs WHERE id = :id"), {"id": pid})
        await session.commit()


@pytest.mark.asyncio
@requires_real_db
async def test_list_kline_sync_failures_only_permanent(session, parent_task_id):
    # Three ts_codes; push two of them past the threshold to mark permanent.
    threshold = 2
    for i in range(threshold):
        await append_kline_sync_failure(
            session, parent_task_id=parent_task_id, ts_code="000001.SZ",
            error="e", permanent_failure_threshold=threshold,
        )
    for i in range(threshold):
        await append_kline_sync_failure(
            session, parent_task_id=parent_task_id, ts_code="000002.SZ",
            error="e", permanent_failure_threshold=threshold,
        )
    # This one stays non-permanent (only 1 failure).
    await append_kline_sync_failure(
        session, parent_task_id=parent_task_id, ts_code="000003.SZ",
        error="e", permanent_failure_threshold=threshold,
    )

    all_rows = await list_kline_sync_failures(session, parent_task_id=parent_task_id)
    assert {r["ts_code"] for r in all_rows} == {"000001.SZ", "000002.SZ", "000003.SZ"}

    perm_rows = await list_kline_sync_failures(
        session, parent_task_id=parent_task_id, only_permanent=True
    )
    assert {r["ts_code"] for r in perm_rows} == {"000001.SZ", "000002.SZ"}
    assert all(r["is_permanent_failure"] is True for r in perm_rows)


# ---------------------------------------------------------------------------
# count_permanent_failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_count_permanent_failures(session, parent_task_id):
    threshold = 3
    # Make 3 ts_codes permanent, 2 non-permanent.
    permanent_codes = ["000001.SZ", "000002.SZ", "000003.SZ"]
    for code in permanent_codes:
        for _ in range(threshold):
            await append_kline_sync_failure(
                session, parent_task_id=parent_task_id, ts_code=code,
                error="e", permanent_failure_threshold=threshold,
            )
    for code in ["000004.SZ", "000005.SZ"]:
        await append_kline_sync_failure(
            session, parent_task_id=parent_task_id, ts_code=code,
            error="e", permanent_failure_threshold=threshold,
        )

    count = await count_permanent_failures(session, parent_task_id=parent_task_id)
    assert count == 3


# ---------------------------------------------------------------------------
# reset_dispatch_failed_batches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_reset_dispatch_failed_batches(session):
    ids = []
    # 3 rows with dispatch_error=true
    for i in range(3):
        rid = await _insert_batch_row(
            session,
            task_id=f"disp-{uuid.uuid4().hex}",
            status="failed",
            payload={"dispatch_error": "true", "dispatch_error_message": f"boom-{i}"},
        )
        ids.append((rid, True))
    # 2 rows failed without dispatch_error
    for _ in range(2):
        rid = await _insert_batch_row(
            session,
            task_id=f"nofail-{uuid.uuid4().hex}",
            status="failed",
            payload={"reason": "data_error"},
        )
        ids.append((rid, False))
    await session.commit()

    try:
        returned = await reset_dispatch_failed_batches(session)
        assert len(returned) == 3

        # Verify the 3 dispatch-error rows are now pending and stripped.
        for rid, was_dispatch in ids:
            row = (
                await session.execute(
                    text(
                        "SELECT status, payload FROM task_runs WHERE id = :id"
                    ),
                    {"id": rid},
                )
            ).mappings().one()
            if was_dispatch:
                assert row["status"] == "pending"
                assert row["payload"].get("dispatch_error") is None
                assert row["payload"].get("dispatch_error_message") is None
            else:
                assert row["status"] == "failed"
                assert row["payload"].get("reason") == "data_error"
    finally:
        for rid, _ in ids:
            await session.execute(text("DELETE FROM task_runs WHERE id = :id"), {"id": rid})
        await session.commit()


# ---------------------------------------------------------------------------
# concurrency (optional)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@requires_real_db
async def test_concurrent_append_does_not_lose_records(parent_task_id):
    """Three concurrent subtasks reporting different ts_codes must all land.

    Uses separate AsyncSession instances because a single AsyncSession is not
    safe to share across concurrent coroutines.
    """
    codes = ["000001.SZ", "000002.SZ", "000003.SZ"]

    async def _append_one(code: str) -> bool:
        async with _temp_session() as s:
            return await append_kline_sync_failure(
                s, parent_task_id=parent_task_id, ts_code=code, error="concurrent"
            )

    results = await asyncio.gather(*[_append_one(c) for c in codes])
    assert results == [False, False, False]  # all first-time inserts, below threshold

    async with _temp_session() as s:
        rows = await list_kline_sync_failures(s, parent_task_id=parent_task_id)
        assert {r["ts_code"] for r in rows} == set(codes)
        assert all(r["failure_count"] == 1 for r in rows)
