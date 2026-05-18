from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Any

from sqlalchemy import text

from app.data.service import (
    default_kline_window,
    infer_incremental_kline_window,
    sync_kline,
    sync_stock_basic,
    sync_trade_calendar,
)
from app.data.stock_service import sync_fundamentals
from app.db.session import async_session_factory
from app.tasks.celery_app import celery_app


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


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
                "payload": json.dumps(_jsonable(payload), ensure_ascii=False, default=str),
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
            "payload": json.dumps(_jsonable(payload), ensure_ascii=False, default=str),
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
            "result": json.dumps(_jsonable(result or {}), ensure_ascii=False, default=str),
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


@celery_app.task(name="app.tasks.data_tasks.update_stock_basic", bind=True)
def update_stock_basic(self) -> dict[str, Any]:
    return asyncio.run(
        _run_tracked(
            "update_stock_basic",
            self.request.id,
            {},
            lambda session: sync_stock_basic(session),
        )
    )


@celery_app.task(name="app.tasks.data_tasks.update_trade_calendar", bind=True)
def update_trade_calendar(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    today = datetime.now(tz=UTC).date()
    start = date.fromisoformat(start_date) if start_date else today - timedelta(days=370)
    end = date.fromisoformat(end_date) if end_date else today + timedelta(days=40)
    return asyncio.run(
        _run_tracked(
            "update_trade_calendar",
            self.request.id,
            {"start_date": start, "end_date": end},
            lambda session: sync_trade_calendar(session, start, end),
        )
    )


@celery_app.task(name="app.tasks.data_tasks.sync_sample_kline", bind=True)
def sync_sample_kline(
    self,
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_kline_window()
    start = date.fromisoformat(start_date) if start_date else default_start
    end = date.fromisoformat(end_date) if end_date else default_end
    return asyncio.run(
        _run_tracked(
            "sync_sample_kline",
            self.request.id,
            {"ts_codes": ts_codes, "start_date": start, "end_date": end},
            lambda session: sync_kline(session, ts_codes, start, end),
        )
    )


@celery_app.task(name="app.tasks.data_tasks.sync_fundamentals", bind=True)
def sync_fundamentals_task(
    self,
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_kline_window()
    start = date.fromisoformat(start_date) if start_date else default_start
    end = date.fromisoformat(end_date) if end_date else default_end
    return asyncio.run(
        _run_tracked(
            "sync_fundamentals",
            self.request.id,
            {"ts_codes": ts_codes, "start_date": start, "end_date": end},
            lambda session: sync_fundamentals(session, ts_codes, start, end),
        )
    )


@celery_app.task(name="app.tasks.data_tasks.incremental_kline_update", bind=True)
def incremental_kline_update(self) -> dict[str, Any]:
    async def run(session) -> dict[str, Any]:
        start, end = await infer_incremental_kline_window(session)
        if start is None or end is None:
            return {"skipped": True, "reason": "no new open trade dates"}
        return await sync_kline(session, None, start, end)

    return asyncio.run(
        _run_tracked(
            "incremental_kline_update",
            self.request.id,
            {},
            run,
        )
    )
