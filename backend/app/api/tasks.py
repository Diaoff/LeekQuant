from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from uuid import uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, Body, Depends, HTTPException, status
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field, model_validator
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repository import (
    create_pending_task_run,
    get_active_stock_codes,
    get_active_task_run,
    get_latest_task_run,
    get_sync_progress,
    mark_task_run_failed,
    mark_stale_running_task_runs,
    mark_task_run_queue_failed,
)
from app.db.session import get_session
from app.preferences.service import get_full_kline_sync_concurrency
from app.tasks.beat_lock import get_beat_lock
from app.tasks.celery_app import celery_app
from app.core.celery_health import cached_active_queues
from app.tasks.data_tasks import kline_sync_dispatch, sync_fundamentals_task, sync_sample_kline

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

logger = logging.getLogger(__name__)

# task_runs rows for the DB-queue dispatch are created by _run_tracked with
# this name (the legacy "incremental_kline_update" batch task no longer exists).
INCREMENTAL_KLINE_TASK_NAME = "kline_sync_dispatch"
FULL_FUNDAMENTALS_TASK_NAME = "sync_fundamentals"
KLINE_SYNC_DISPATCH_BEAT_LOCK = "app.tasks.data_tasks.kline_sync_dispatch"
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
    ts_codes: list[str] | None = Field(default=None, max_length=2000)
    concurrency: int | None = Field(default=None, ge=1, le=8)
    batch_size: int | None = Field(default=None, ge=1, le=2000)


class FundamentalsTaskRequest(BaseModel):
    ts_codes: list[str] | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    concurrency: int | None = Field(default=None, ge=1, le=8)


def _celery_inspector():
    try:
        return celery_app.control.inspect(timeout=1.0)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"celery worker health check failed: {exc}",
        ) from exc


def _active_celery_worker_names() -> list[str]:
    active_queues = cached_active_queues()
    if not active_queues:
        return []
    return sorted(
        worker_name
        for worker_name, queues in active_queues.items()
        if any(queue.get("name") == "data" for queue in queues or [])
    )


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


async def _guard_fundamentals_sync(session: AsyncSession) -> None:
    await _guard_exclusive_data_sync(
        session,
        task_names=[FULL_FUNDAMENTALS_TASK_NAME],
        stale_after=FULL_FUNDAMENTALS_STALE_AFTER,
        task_label="fundamentals sync",
    )


async def _guard_beat_lock_free(task_name: str) -> None:
    """Refuse to dispatch a beat-locked task while the scheduled run holds the lock.

    Prevents the "phantom success" bug: previously the API created a ``task_runs``
    pending row and dispatched a beat-locked task; if the daily beat already held
    the lock, the task returned ``None`` and the success signal marked the row
    ``success`` with zero batches. Checking here returns a clear 409 (and creates
    NO row) when the lock is held. Fails open on Redis errors so a transient Redis
    blip never blocks a legitimate user-triggered sync.
    """
    try:
        if get_beat_lock().is_locked(task_name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{task_name} 正由定时任务执行（beat lock 占用），请稍后重试",
            )
    except RedisError:
        # Fail-open: if we can't reach Redis, let the dispatch proceed and let the
        # task itself handle lock contention (it will raise BeatLockSkipped).
        logger.debug("silent except in _guard_beat_lock_free")
        pass


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
) -> dict[str, str]:
    """Dispatch a full-history K-line sync via the DB-queue architecture.

    Creates a ``kline_sync_jobs`` row (job_type='full') inside the
    ``kline_sync_dispatch`` task, which also computes per-stock ranges and
    starts workers. The beat lock prevents concurrent dispatches with the
    daily incremental beat.
    """
    await _guard_beat_lock_free(KLINE_SYNC_DISPATCH_BEAT_LOCK)
    payload: dict[str, Any] = {
        "job_type": "full",
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "end_date": request.end_date.isoformat() if request.end_date else None,
    }
    try:
        result = kline_sync_dispatch.apply_async(kwargs=payload)
    except OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": result.id, "status": "dispatched"}


@router.post("/data/incremental-kline")
async def start_incremental_kline_task(
    request: IncrementalKlineTaskRequest = Body(default_factory=IncrementalKlineTaskRequest),
) -> dict[str, str]:
    """Dispatch an incremental K-line sync via the DB-queue architecture.

    Creates a ``kline_sync_jobs`` row (job_type='incremental') inside the
    ``kline_sync_dispatch`` task, which also computes per-stock gap ranges and
    starts workers. The beat lock prevents concurrent dispatches with the daily
    incremental beat.
    """
    await _guard_beat_lock_free(KLINE_SYNC_DISPATCH_BEAT_LOCK)
    payload: dict[str, Any] = {"job_type": "incremental"}
    if request.ts_codes is not None:
        payload["ts_codes"] = request.ts_codes
    try:
        result = kline_sync_dispatch.apply_async(kwargs=payload)
    except OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": result.id, "status": "dispatched"}


@router.post("/data/incremental-kline/catchup")
async def start_incremental_kline_catchup(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Dispatch incremental K-line sync for NOT caught up stocks only.

    Uses ``get_sync_progress`` to find stocks that are behind, then filters
    out suspended stocks (``is_suspended = TRUE`` on their latest trading
    day), and dispatches a ``kline_sync_dispatch`` with only those codes.
    """
    await _guard_beat_lock_free(KLINE_SYNC_DISPATCH_BEAT_LOCK)
    progress = await get_sync_progress(session)
    codes = list(progress["not_caught_up_codes"])
    if not codes:
        return {"task_id": None, "status": "noop", "reason": "all stocks caught up"}

    active = await get_active_stock_codes(session, codes)
    if not active:
        return {"task_id": None, "status": "noop", "reason": "all remaining stocks are suspended"}

    payload: dict[str, Any] = {"job_type": "incremental", "ts_codes": active}
    try:
        result = kline_sync_dispatch.apply_async(kwargs=payload)
    except OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": result.id, "status": "dispatched", "codes": active}


@router.get("/data/sync-progress")
async def sync_progress(
    ts_codes: str | None = None,
    watchlist_id: int | None = None,
    recent_run: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Report K-line sync progress straight from the database (source of truth).

    Independent of any Celery task status, so it answers "did everything actually
    sync?" even after a task is killed by a time limit. A stock is "caught up"
    when its latest K-line date reaches the latest open trading day.

    Scope (first match wins):
      * ``ts_codes``  - comma-separated explicit list
      * ``watchlist_id`` - a watchlist group id
      * ``recent_run=true`` - the codes targeted by the most recent incremental run
      * none - all stocks
    """
    codes: list[str] | None = None
    if ts_codes:
        codes = [c.strip().upper() for c in ts_codes.split(",") if c.strip()]

    if not codes and watchlist_id is None and recent_run:
        last_run = await get_latest_task_run(session, task_name=INCREMENTAL_KLINE_TASK_NAME)
        if last_run:
            payload = last_run.get("payload") or {}
            recent_codes = payload.get("ts_codes")
            if recent_codes:
                codes = [str(c).strip().upper() for c in recent_codes]

    try:
        progress = await asyncio.wait_for(
            get_sync_progress(session, ts_codes=codes, watchlist_id=watchlist_id),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="sync progress query timed out after 10s",
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Do NOT silently return zeros: an all-zero progress looks like valid
        # data and masks real bugs (e.g. SQL type errors). Surface the failure.
        logger.exception("sync_progress query failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"sync progress query failed: {exc.__class__.__name__}",
        )
    progress["scope"] = {
        "ts_codes": codes,
        "watchlist_id": watchlist_id,
        "recent_run": bool(recent_run and codes is not None),
    }
    return progress


@router.post("/data/fundamentals")
async def start_fundamentals_task(
    request: FundamentalsTaskRequest = Body(default_factory=FundamentalsTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await _guard_beat_lock_free("app.tasks.data_tasks.sync_fundamentals")
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
