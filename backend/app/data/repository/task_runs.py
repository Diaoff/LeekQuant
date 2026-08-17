from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_pending_task_run(
    session: AsyncSession,
    *,
    task_name: str,
    task_id: str,
    payload: dict[str, Any] | None = None,
) -> int:
    result = await session.execute(
        text(
            """
            INSERT INTO task_runs (task_name, task_id, status, payload)
            VALUES (:task_name, :task_id, 'pending', CAST(:payload AS JSONB))
            RETURNING id
            """
        ),
        {
            "task_name": task_name,
            "task_id": task_id,
            "payload": json.dumps(payload or {}, ensure_ascii=False, default=str),
        },
    )
    await session.commit()
    return int(result.scalar_one())


async def get_active_task_run(session: AsyncSession, *, task_names: list[str]) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT id, task_name, task_id, status, started_at, payload
            FROM task_runs
            WHERE task_name = ANY(:task_names)
              AND status IN ('pending', 'running')
            ORDER BY started_at DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"task_names": task_names},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def mark_stale_running_task_runs(
    session: AsyncSession,
    *,
    older_than: timedelta = timedelta(hours=24),
    task_names: list[str] | None = None,
    error_message: str = "stale running task after celery worker cleanup",
) -> int:
    task_name_filter = ""
    cutoff_at = datetime.now(tz=UTC) - older_than
    params: dict[str, Any] = {
        "cutoff_at": cutoff_at,
        "error_message": error_message[:4000],
    }
    if task_names:
        task_name_filter = "AND task_name = ANY(CAST(:task_names AS TEXT[]))"
        params["task_names"] = task_names

    result = await session.execute(
        text(
            f"""
            UPDATE task_runs
            SET status = 'failed',
                finished_at = NOW(),
                error_message = COALESCE(error_message, :error_message)
            WHERE status = 'running'
              AND started_at < :cutoff_at
              {task_name_filter}
            RETURNING id
            """
        ),
        params,
    )
    rows = result.fetchall()
    await session.commit()
    return len(rows)


async def mark_task_run_queue_failed(
    session: AsyncSession,
    *,
    task_id: str,
    error_message: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE task_runs
            SET status = 'failed',
                finished_at = NOW(),
                error_message = :error_message
            WHERE task_id = :task_id
            """
        ),
        {
            "task_id": task_id,
            "error_message": error_message[:4000],
        },
    )
    await session.commit()


async def mark_task_run_failed(
    session: AsyncSession,
    *,
    task_id: str,
    error_message: str,
    statuses: list[str] | None = None,
) -> None:
    status_filter = ""
    params: dict[str, Any] = {
        "task_id": task_id,
        "error_message": error_message[:4000],
    }
    if statuses:
        status_filter = "AND status = ANY(CAST(:statuses AS TEXT[]))"
        params["statuses"] = statuses

    await session.execute(
        text(
            f"""
            UPDATE task_runs
            SET status = 'failed',
                finished_at = NOW(),
                error_message = COALESCE(error_message, :error_message)
            WHERE task_id = :task_id
              {status_filter}
            """
        ),
        params,
    )
    await session.commit()


async def mark_task_run_cancelled(
    session: AsyncSession,
    *,
    task_id: str,
    error_message: str,
) -> None:
    """Mark a task_runs row as cancelled (e.g. beat lock skipped).

    Only touches non-terminal rows so a status the task body already wrote is
    never overwritten.
    """
    await session.execute(
        text(
            """
            UPDATE task_runs
            SET status = 'cancelled',
                finished_at = NOW(),
                error_message = COALESCE(error_message, :error_message)
            WHERE task_id = :task_id
              AND status IN ('pending', 'running')
            """
        ),
        {
            "task_id": task_id,
            "error_message": error_message[:4000],
        },
    )
    await session.commit()


async def reconcile_task_run_status(
    session: AsyncSession,
    *,
    task_id: str,
    status: str,
    error_message: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """Backstop reconciliation of a task_runs row to Celery's terminal state.

    Called from the Celery ``task_failure`` / ``task_success`` / ``task_revoked``
    signals. Idempotent: only updates rows still in ``status IN ('pending', 'running')``,
    so it never overwrites a status the task body already wrote.
    """
    await session.execute(
        text(
            """
            UPDATE task_runs
            SET status = :status,
                finished_at = COALESCE(finished_at, NOW()),
                error_message = COALESCE(error_message, :error_message),
                result = COALESCE(CAST(:result AS JSONB), result)
            WHERE task_id = :task_id
              AND status IN ('pending', 'running')
            """
        ),
        {
            "task_id": task_id,
            "status": status,
            "error_message": error_message[:4000] if error_message else None,
            "result": json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
        },
    )
    await session.commit()


async def get_latest_task_run(
    session: AsyncSession,
    *,
    task_name: str,
) -> dict[str, Any] | None:
    """Return the most recent task_runs row for ``task_name`` (any status)."""
    result = await session.execute(
        text(
            """
            SELECT id, task_name, task_id, status, started_at, finished_at,
                   duration_ms, payload, result, error_message
            FROM task_runs
            WHERE task_name = :task_name
            ORDER BY started_at DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"task_name": task_name},
    )
    row = result.mappings().first()
    return dict(row) if row else None
