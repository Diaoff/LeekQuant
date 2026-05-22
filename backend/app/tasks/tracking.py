"""Shared task_runs tracking helpers for Celery maintenance tasks."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any

from sqlalchemy import text

from app.db.session import async_session_factory


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, default=str)


async def _claim_task_run(session, task_name: str, task_id: str | None, payload: dict[str, Any]) -> int:
    if task_id:
        result = await session.execute(
            text(
                """
                UPDATE task_runs
                SET status = 'running',
                    started_at = NOW(),
                    finished_at = NULL,
                    duration_ms = NULL,
                    payload = CAST(:payload AS JSONB),
                    result = '{}'::JSONB,
                    error_message = NULL
                WHERE task_id = :task_id
                  AND task_name = :task_name
                  AND status = 'pending'
                RETURNING id
                """
            ),
            {
                "task_name": task_name,
                "task_id": task_id,
                "payload": _json_dumps(payload),
            },
        )
        run_id = result.scalar_one_or_none()
        if run_id is not None:
            await session.commit()
            return int(run_id)

    result = await session.execute(
        text(
            """
            INSERT INTO task_runs (task_name, task_id, status, payload)
            VALUES (:task_name, :task_id, 'running', CAST(:payload AS JSONB))
            RETURNING id
            """
        ),
        {
            "task_name": task_name,
            "task_id": task_id,
            "payload": _json_dumps(payload),
        },
    )
    await session.commit()
    return int(result.scalar_one())


async def _finish_task_run(
    session,
    run_id: int,
    status: str,
    duration_ms: int,
    *,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            UPDATE task_runs
            SET status = :status,
                finished_at = NOW(),
                duration_ms = :duration_ms,
                result = CAST(:result AS JSONB),
                error_message = :error_message
            WHERE id = :run_id
            """
        ),
        {
            "run_id": run_id,
            "status": status,
            "duration_ms": duration_ms,
            "result": _json_dumps(result or {}),
            "error_message": error_message,
        },
    )
    await session.commit()


async def _run_tracked(
    task_name: str,
    task_id: str | None,
    payload: dict[str, Any],
    fn: Callable[[Any], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    started = perf_counter()
    async with async_session_factory() as session:
        run_id = await _claim_task_run(session, task_name, task_id, payload)
        try:
            result = await fn(session)
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            await _finish_task_run(session, run_id, "failed", duration_ms, error_message=str(exc))
            raise
        duration_ms = int((perf_counter() - started) * 1000)
        await _finish_task_run(session, run_id, "success", duration_ms, result=result)
        return _jsonable(result)
