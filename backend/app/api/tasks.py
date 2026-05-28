from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, Body, Depends, HTTPException, status
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repository import (
    create_pending_task_run,
    get_active_task_run,
    mark_task_run_failed,
    mark_stale_running_task_runs,
    mark_task_run_queue_failed,
)
from app.db.session import get_session
from app.preferences.service import get_full_kline_sync_concurrency
from app.tasks.celery_app import celery_app
from app.tasks.data_tasks import incremental_kline_update, sync_all_kline, sync_fundamentals_task, sync_sample_kline
from app.tasks.factor_tasks import analyze_factor_icir_task, compute_daily_factors

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

FULL_KLINE_TASK_NAME = "sync_all_kline"
INCREMENTAL_KLINE_TASK_NAME = "incremental_kline_update"
FULL_FUNDAMENTALS_TASK_NAME = "sync_fundamentals"
FULL_KLINE_STALE_AFTER = timedelta(hours=24)
FULL_FUNDAMENTALS_STALE_AFTER = timedelta(hours=24)


class SampleKlineTaskRequest(BaseModel):
    ts_codes: list[str] | None = Field(default=None, max_length=30)
    start_date: date | None = None
    end_date: date | None = None
    concurrency: int | None = Field(default=None, ge=1, le=8)


class FullKlineTaskRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    concurrency: int | None = Field(default=None, ge=1, le=8)


class IncrementalKlineTaskRequest(BaseModel):
    concurrency: int | None = Field(default=None, ge=1, le=8)


class FundamentalsTaskRequest(BaseModel):
    ts_codes: list[str] | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    concurrency: int | None = Field(default=None, ge=1, le=8)


class FactorComputeTaskRequest(BaseModel):
    trade_date: date | None = None
    scope_type: str = Field(default="all", pattern="^(all|watchlist_group)$")
    scope_value: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_scope(self) -> "FactorComputeTaskRequest":
        if self.scope_type == "all":
            self.scope_value = None
            return self
        if not self.scope_value or not self.scope_value.strip():
            raise ValueError("scope_value is required for watchlist_group scope")
        self.scope_value = self.scope_value.strip()
        return self


class FactorAnalyzeTaskRequest(BaseModel):
    factor_name: str = Field(min_length=1, max_length=64)
    period_start: date
    period_end: date
    forward_days: int = Field(default=5, ge=1, le=60)


def _celery_inspector():
    try:
        return celery_app.control.inspect(timeout=1.0)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"celery worker health check failed: {exc}",
        ) from exc


def _active_celery_worker_names() -> list[str]:
    try:
        stats = _celery_inspector().stats()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"celery worker health check failed: {exc}",
        ) from exc
    if not stats:
        return []
    return sorted(stats)


def _celery_task_ids_by_state() -> set[str]:
    inspector = _celery_inspector()
    task_ids: set[str] = set()
    try:
        snapshots = [
            inspector.active() or {},
            inspector.reserved() or {},
            inspector.scheduled() or {},
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"celery task health check failed: {exc}",
        ) from exc

    for worker_tasks in snapshots:
        for tasks in worker_tasks.values():
            for task in tasks or []:
                request = task.get("request", task)
                task_id = request.get("id") or task.get("id")
                if task_id:
                    task_ids.add(str(task_id))
    return task_ids


async def _guard_exclusive_data_sync(
    session: AsyncSession,
    *,
    task_names: list[str],
    stale_after: timedelta,
    task_label: str,
) -> None:
    await mark_stale_running_task_runs(
        session,
        older_than=stale_after,
        task_names=task_names,
        error_message=f"stale {task_label} after celery worker cleanup",
    )

    worker_names = _active_celery_worker_names()
    if len(worker_names) == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"no active celery worker found; restart celery before {task_label}",
        )
    if len(worker_names) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"multiple celery workers detected before {task_label}; "
                f"restart celery to keep a single worker active: {', '.join(worker_names)}"
            ),
        )

    active_task = await get_active_task_run(session, task_names=task_names)
    if active_task is None:
        return

    active_task_id = active_task.get("task_id")
    if active_task_id and active_task_id not in _celery_task_ids_by_state():
        await mark_task_run_failed(
            session,
            task_id=str(active_task_id),
            error_message=f"orphaned {task_label} after celery worker restart",
            statuses=["pending", "running"],
        )
        return

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"{task_label} is already pending or running: "
            f"{active_task['task_id']} ({active_task['status']})"
        ),
    )


async def _guard_full_kline_sync(session: AsyncSession) -> None:
    await _guard_exclusive_data_sync(
        session,
        task_names=[FULL_KLINE_TASK_NAME, INCREMENTAL_KLINE_TASK_NAME],
        stale_after=FULL_KLINE_STALE_AFTER,
        task_label="full kline sync",
    )


async def _guard_incremental_kline_sync(session: AsyncSession) -> None:
    await _guard_exclusive_data_sync(
        session,
        task_names=[INCREMENTAL_KLINE_TASK_NAME, FULL_KLINE_TASK_NAME],
        stale_after=FULL_KLINE_STALE_AFTER,
        task_label="incremental kline sync",
    )


async def _guard_fundamentals_sync(session: AsyncSession) -> None:
    await _guard_exclusive_data_sync(
        session,
        task_names=[FULL_FUNDAMENTALS_TASK_NAME],
        stale_after=FULL_FUNDAMENTALS_STALE_AFTER,
        task_label="fundamentals sync",
    )


@router.post("/data/sample-kline")
async def start_sample_kline_task(
    request: SampleKlineTaskRequest = Body(default_factory=SampleKlineTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    task_id = uuid4().hex
    effective_concurrency = request.concurrency
    if effective_concurrency is None:
        effective_concurrency = await get_full_kline_sync_concurrency(session)
    payload = {
        "ts_codes": request.ts_codes,
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "end_date": request.end_date.isoformat() if request.end_date else None,
        "concurrency": effective_concurrency,
    }
    await create_pending_task_run(
        session,
        task_name="sync_sample_kline",
        task_id=task_id,
        payload=payload,
    )
    try:
        sync_sample_kline.apply_async(
            kwargs=payload,
            task_id=task_id,
        )
    except OperationalError as exc:
        await mark_task_run_queue_failed(session, task_id=task_id, error_message=f"task queue unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": task_id, "status": "pending"}


@router.post("/data/sync-all-kline")
async def start_sync_all_kline_task(
    request: FullKlineTaskRequest = Body(default_factory=FullKlineTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await _guard_full_kline_sync(session)
    task_id = uuid4().hex
    effective_concurrency = request.concurrency
    if effective_concurrency is None:
        effective_concurrency = await get_full_kline_sync_concurrency(session)
    payload = {
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "end_date": request.end_date.isoformat() if request.end_date else None,
        "concurrency": effective_concurrency,
    }
    await create_pending_task_run(
        session,
        task_name="sync_all_kline",
        task_id=task_id,
        payload=payload,
    )
    try:
        sync_all_kline.apply_async(
            kwargs=payload,
            task_id=task_id,
        )
    except OperationalError as exc:
        await mark_task_run_queue_failed(session, task_id=task_id, error_message=f"task queue unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": task_id, "status": "pending"}


@router.post("/data/incremental-kline")
async def start_incremental_kline_task(
    request: IncrementalKlineTaskRequest = Body(default_factory=IncrementalKlineTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await _guard_incremental_kline_sync(session)
    task_id = uuid4().hex
    effective_concurrency = request.concurrency
    if effective_concurrency is None:
        effective_concurrency = await get_full_kline_sync_concurrency(session)
    payload = {"concurrency": effective_concurrency}
    await create_pending_task_run(
        session,
        task_name="incremental_kline_update",
        task_id=task_id,
        payload=payload,
    )
    try:
        incremental_kline_update.apply_async(kwargs=payload, task_id=task_id)
    except OperationalError as exc:
        await mark_task_run_queue_failed(session, task_id=task_id, error_message=f"task queue unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": task_id, "status": "pending"}


@router.post("/data/fundamentals")
async def start_fundamentals_task(
    request: FundamentalsTaskRequest = Body(default_factory=FundamentalsTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await _guard_fundamentals_sync(session)
    task_id = uuid4().hex
    effective_concurrency = request.concurrency
    if effective_concurrency is None:
        effective_concurrency = await get_full_kline_sync_concurrency(session)
    payload = {
        "ts_codes": request.ts_codes,
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "end_date": request.end_date.isoformat() if request.end_date else None,
        "concurrency": effective_concurrency,
    }
    await create_pending_task_run(
        session,
        task_name=FULL_FUNDAMENTALS_TASK_NAME,
        task_id=task_id,
        payload=payload,
    )
    try:
        sync_fundamentals_task.apply_async(
            kwargs=payload,
            task_id=task_id,
        )
    except OperationalError as exc:
        await mark_task_run_queue_failed(session, task_id=task_id, error_message=f"task queue unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": task_id, "status": "pending"}


@router.post("/factors/compute")
async def start_factor_compute_task(
    request: FactorComputeTaskRequest = Body(default_factory=FactorComputeTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    task_id = uuid4().hex
    payload = {
        "trade_date": request.trade_date.isoformat() if request.trade_date else None,
        "scope_type": request.scope_type,
        "scope_value": request.scope_value,
    }
    await create_pending_task_run(
        session,
        task_name="compute_daily_factors",
        task_id=task_id,
        payload=payload,
    )
    try:
        compute_daily_factors.apply_async(kwargs=payload, task_id=task_id)
    except OperationalError as exc:
        await mark_task_run_queue_failed(session, task_id=task_id, error_message=f"task queue unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": task_id, "status": "pending"}


@router.post("/factors/analyze")
async def start_factor_analyze_task(
    request: FactorAnalyzeTaskRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    task_id = uuid4().hex
    payload = {
        "factor_name": request.factor_name,
        "period_start": request.period_start.isoformat(),
        "period_end": request.period_end.isoformat(),
        "forward_days": request.forward_days,
    }
    await create_pending_task_run(
        session,
        task_name="analyze_factor_icir",
        task_id=task_id,
        payload=payload,
    )
    try:
        analyze_factor_icir_task.apply_async(kwargs=payload, task_id=task_id)
    except OperationalError as exc:
        await mark_task_run_queue_failed(session, task_id=task_id, error_message=f"task queue unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": task_id, "status": "pending"}


@router.get("/recent")
async def recent_tasks(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(
        text(
            """
            SELECT id, task_name, task_id, status, started_at, finished_at, duration_ms, payload, result, error_message
            FROM task_runs
            ORDER BY started_at DESC
            LIMIT 20
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


@router.get("/{task_id}")
async def task_status(task_id: str) -> dict:
    async_result = AsyncResult(task_id, app=celery_app)
    task_status_value = async_result.status.lower()
    payload = {
        "task_id": task_id,
        "status": task_status_value,
        "ready": async_result.ready(),
    }
    info = async_result.info
    if isinstance(info, dict) and "current" in info:
        payload["meta"] = info
    if async_result.ready():
        if async_result.failed() or task_status_value == "revoked":
            payload["error"] = str(async_result.result)
        else:
            result = async_result.result
            payload["result"] = result if isinstance(result, dict) else str(result)
    return payload
