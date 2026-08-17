from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_kline_sync_job(
    session: AsyncSession,
    *,
    job_type: str,
    config: dict[str, Any] | None = None,
) -> int:
    """Insert a kline_sync_jobs row (status='running') and return its id."""
    result = await session.execute(
        text(
            """
            INSERT INTO kline_sync_jobs (job_type, status, config, started_at)
            VALUES (:job_type, 'running', CAST(:config AS JSONB), NOW())
            RETURNING id
            """
        ),
        {
            "job_type": job_type,
            "config": json.dumps(config or {}, ensure_ascii=False, default=str),
        },
    )
    job_id = int(result.scalar_one())
    await session.commit()
    return job_id


async def insert_kline_sync_items(
    session: AsyncSession,
    *,
    job_id: int,
    items: list[dict[str, Any]],
) -> int:
    """Bulk-insert work items for a job and bump the job's scope_total."""
    if not items:
        await session.commit()
        return 0
    values = [
        {
            "job_id": job_id,
            "ts_code": item["ts_code"],
            "start_date": item["start_date"],
            "end_date": item["end_date"],
        }
        for item in items
    ]
    await session.execute(
        text(
            """
            INSERT INTO kline_sync_items (job_id, ts_code, start_date, end_date, status)
            VALUES (:job_id, :ts_code, :start_date, :end_date, 'pending')
            ON CONFLICT (job_id, ts_code, start_date, end_date) DO NOTHING
            """
        ),
        values,
    )
    await session.execute(
        text(
            """
            UPDATE kline_sync_jobs
            SET scope_total = (SELECT COUNT(*) FROM kline_sync_items WHERE job_id = :job_id)
            WHERE id = :job_id
            """
        ),
        {"job_id": job_id},
    )
    await session.commit()
    return len(values)


async def claim_kline_sync_items(
    session: AsyncSession,
    *,
    job_id: int,
    count: int,
    worker_id: str,
) -> list[dict[str, Any]]:
    """Atomically claim up to ``count`` pending items for a worker.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers never claim the same
    item. Claiming increments ``attempts`` (a claim IS an attempt) and stamps
    ``last_attempt_at`` for stuck-item detection.
    """
    result = await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = 'running',
                worker_id = :worker_id,
                attempts = attempts + 1,
                last_attempt_at = NOW()
            WHERE id IN (
                SELECT id
                FROM kline_sync_items
                WHERE job_id = :job_id AND status = 'pending'
                ORDER BY id
                LIMIT :count
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, ts_code, start_date, end_date, attempts
            """
        ),
        {"job_id": job_id, "count": max(1, count), "worker_id": worker_id},
    )
    rows = [dict(row) for row in result.mappings().all()]
    await session.commit()
    return rows


async def mark_item_done(
    session: AsyncSession,
    *,
    item_id: int,
    job_id: int,
) -> None:
    """Mark an item done and bump the job's scope_done counter."""
    await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = 'done', worker_id = NULL, last_error = NULL
            WHERE id = :item_id
            """
        ),
        {"item_id": item_id},
    )
    await session.execute(
        text("UPDATE kline_sync_jobs SET scope_done = scope_done + 1 WHERE id = :job_id"),
        {"job_id": job_id},
    )
    await session.commit()


async def mark_item_failed(
    session: AsyncSession,
    *,
    item_id: int,
    job_id: int,
    error: str,
    max_attempts: int,
) -> bool:
    """Record a failure for an item.

    ``attempts`` is NOT incremented here — ``claim_kline_sync_items`` already
    counted this attempt; incrementing again would double-count each failure.
    Returns True when the item crossed ``max_attempts`` and became
    ``permanently_failed`` (job counters updated accordingly).
    """
    result = await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = CASE
                    WHEN attempts >= :max_attempts THEN 'permanently_failed'
                    ELSE 'pending'
                END,
                worker_id = NULL,
                last_error = :error
            WHERE id = :item_id
            RETURNING status, ts_code
            """
        ),
        {"item_id": item_id, "max_attempts": max_attempts, "error": error[:4000]},
    )
    row = result.mappings().one_or_none()
    is_permanent = bool(row and row["status"] == "permanently_failed")
    if is_permanent:
        await session.execute(
            text(
                """
                UPDATE kline_sync_jobs
                SET scope_failed = scope_failed + 1,
                    permanent_failure_codes = array_append(
                        COALESCE(permanent_failure_codes, ARRAY[]::TEXT[]), :ts_code
                    )
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id, "ts_code": row["ts_code"]},
        )
    await session.commit()
    return is_permanent


async def recover_stuck_items(
    session: AsyncSession,
    *,
    stuck_seconds: int,
) -> int:
    """Reset 'running' items whose last attempt is older than ``stuck_seconds``."""
    result = await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = 'pending', worker_id = NULL
            WHERE status = 'running'
              AND (
                  last_attempt_at IS NULL
                  OR last_attempt_at < NOW() - make_interval(secs => :stuck_seconds)
              )
            RETURNING id
            """
        ),
        {"stuck_seconds": stuck_seconds},
    )
    rows = result.fetchall()
    await session.commit()
    return len(rows)


async def complete_job_if_done(
    session: AsyncSession,
    *,
    job_id: int,
) -> bool:
    """Mark the job completed when no pending/running items remain."""
    result = await session.execute(
        text(
            """
            UPDATE kline_sync_jobs
            SET status = 'completed', completed_at = NOW()
            WHERE id = :job_id
              AND status = 'running'
              AND NOT EXISTS (
                  SELECT 1 FROM kline_sync_items
                  WHERE job_id = :job_id AND status IN ('pending', 'running')
              )
            RETURNING id
            """
        ),
        {"job_id": job_id},
    )
    completed = result.first() is not None
    await session.commit()
    return completed


async def get_job_progress(
    session: AsyncSession,
    *,
    job_id: int,
) -> dict[str, Any] | None:
    """Return a job row merged with live per-status item counts."""
    result = await session.execute(
        text(
            """
            SELECT
                j.id, j.job_type, j.status,
                j.scope_total, j.scope_done, j.scope_failed,
                j.permanent_failure_codes, j.config,
                j.created_at, j.started_at, j.completed_at, j.error,
                COUNT(i.id)::INT AS item_total,
                COUNT(*) FILTER (WHERE i.status = 'pending')::INT AS pending,
                COUNT(*) FILTER (WHERE i.status = 'running')::INT AS running,
                COUNT(*) FILTER (WHERE i.status = 'done')::INT AS done,
                COUNT(*) FILTER (WHERE i.status = 'permanently_failed')::INT AS permanently_failed
            FROM kline_sync_jobs j
            LEFT JOIN kline_sync_items i ON i.job_id = j.id
            WHERE j.id = :job_id
            GROUP BY j.id
            """
        ),
        {"job_id": job_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    progress = dict(row)
    progress["permanent_failure_codes"] = list(progress.get("permanent_failure_codes") or [])
    return progress


async def list_job_items(
    session: AsyncSession,
    *,
    job_id: int,
    status: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List items for a job, optionally filtered by status."""
    status_filter = ""
    params: dict[str, Any] = {"job_id": job_id, "limit": max(1, min(limit, 1000))}
    if status is not None:
        status_filter = "AND status = :status"
        params["status"] = status
    result = await session.execute(
        text(
            f"""
            SELECT id, ts_code, start_date, end_date, status, attempts,
                   last_error, last_attempt_at, worker_id
            FROM kline_sync_items
            WHERE job_id = :job_id
              {status_filter}
            ORDER BY id
            LIMIT :limit
            """
        ),
        params,
    )
    items = [dict(row) for row in result.mappings().all()]
    return {"job_id": job_id, "items": items, "count": len(items)}


async def list_recent_jobs(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List recent jobs (newest first) with live per-status item counts."""
    result = await session.execute(
        text(
            """
            SELECT
                j.id, j.job_type, j.status,
                j.scope_total, j.scope_done, j.scope_failed,
                j.permanent_failure_codes, j.config,
                j.created_at, j.started_at, j.completed_at, j.error,
                COUNT(i.id)::INT AS item_total,
                COUNT(*) FILTER (WHERE i.status = 'pending')::INT AS pending,
                COUNT(*) FILTER (WHERE i.status = 'running')::INT AS running,
                COUNT(*) FILTER (WHERE i.status = 'done')::INT AS done,
                COUNT(*) FILTER (WHERE i.status = 'permanently_failed')::INT AS permanently_failed
            FROM kline_sync_jobs j
            LEFT JOIN kline_sync_items i ON i.job_id = j.id
            GROUP BY j.id
            ORDER BY j.created_at DESC, j.id DESC
            LIMIT :limit
            """
        ),
        {"limit": max(1, min(limit, 100))},
    )
    jobs = []
    for row in result.mappings().all():
        job = dict(row)
        job["permanent_failure_codes"] = list(job.get("permanent_failure_codes") or [])
        jobs.append(job)
    return jobs
