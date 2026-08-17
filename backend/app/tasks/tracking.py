"""Shared task_runs tracking helpers for Celery maintenance tasks."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from time import perf_counter
from typing import Any

from sqlalchemy import text

from app.db.session import async_session_factory
import logging
logger = logging.getLogger(__name__)


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


def with_session(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    **kwargs: Any,
) -> Callable[[Any], Awaitable[Any]]:
    """Wrap an async fn(session, *args, **kwargs) so it accepts a session_factory.

    The wrapper opens a fresh session via ``async with session_factory() as session:``
    and forwards it to ``fn``. This decouples the task body's session from
    the tracker_session used by ``_run_tracked`` for status bookkeeping,
    so calling ``session.close()`` inside the task body no longer breaks
    the final ``_finish_task_run`` call.
    """

    @wraps(fn)
    async def wrapper(session_factory: Any) -> Any:
        async with session_factory() as session:
            return await fn(session, *args, **kwargs)

    return wrapper


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
    """Run a tracked Celery task body with status bookkeeping.

    The tracker_session is reserved exclusively for ``_claim_task_run`` /
    ``_finish_task_run``. ``fn`` receives ``async_session_factory`` (a
    callable returning an async context manager) and is expected to open
    its own session via ``async with session_factory() as session:``.
    This decouples the task body's session lifecycle from the tracker
    session — previously, calling ``session.close()`` inside the task body
    would invalidate the same session used by ``_finish_task_run``,
    leaving task_runs stuck in ``running`` status.
    """
    started = perf_counter()
    async with async_session_factory() as tracker_session:
        run_id = await _claim_task_run(tracker_session, task_name, task_id, payload)
        try:
            result = await fn(async_session_factory)
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            # A task may attach a structured result to its exception via a
            # ``result`` attribute so the task_runs row is marked failed yet
            # retains detail for retry.
            extra_result = getattr(exc, "result", None)
            try:
                await tracker_session.rollback()
            except Exception as rollback_exc:  # pragma: no cover - original failure is more actionable.
                logger.warning("silent except in _run_tracked (rollback_exc)", exc_info=True)
                exc.add_note(f"Failed to rollback task session: {rollback_exc}")
            try:
                await _finish_task_run(
                    tracker_session,
                    run_id,
                    "failed",
                    duration_ms,
                    result=extra_result,
                    error_message=str(exc),
                )
            except Exception as finish_exc:
                logger.warning("silent except in _run_tracked (finish_exc)", exc_info=True)
                exc.add_note(f"Failed to record task failure: {finish_exc}")
            raise
        duration_ms = int((perf_counter() - started) * 1000)
        final_status = "success"
        if isinstance(result, dict) and result.get("_task_status") in ("dispatched", "success"):
            final_status = result.pop("_task_status")
        await _finish_task_run(tracker_session, run_id, final_status, duration_ms, result=result)
        return _jsonable(result)
