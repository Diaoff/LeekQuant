from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, Body, Depends, HTTPException, status
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repository import (
    create_pending_task_run,
    get_job_progress,
    list_job_items,
    list_recent_jobs,
    mark_task_run_queue_failed,
)
from app.data.service import get_data_status, sync_stock_basic, sync_trade_calendar
from app.db.session import async_session_factory, get_session
from app.tasks.celery_app import celery_app
from app.tasks.data_tasks import sync_sample_kline

router = APIRouter(prefix="/api/data", tags=["data"])


class TradeCalendarSyncRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None


class KlineSyncRequest(BaseModel):
    ts_codes: list[str] | None = Field(default=None, max_length=30)
    start_date: date | None = None
    end_date: date | None = None


@router.get("/status")
async def data_status(session: AsyncSession = Depends(get_session)) -> dict:
    return await get_data_status(session)


@router.get("/kline-sync/jobs")
async def list_kline_sync_jobs(session: AsyncSession = Depends(get_session)) -> dict:
    """List recent kline sync jobs with progress."""
    jobs = await list_recent_jobs(session, limit=20)
    return {"jobs": jobs}


@router.get("/kline-sync/jobs/{job_id}")
async def get_kline_sync_job(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Get a single kline sync job with progress."""
    return await get_job_progress(session, job_id=job_id)


@router.get("/kline-sync/jobs/{job_id}/items")
async def get_kline_sync_job_items(
    job_id: int,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List items for a kline sync job."""
    return await list_job_items(session, job_id=job_id, status=status, limit=200)


@router.post("/sync/stock-basic")
async def sync_stock_basic_endpoint(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await sync_stock_basic(session)
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/sync/trade-calendar")
async def sync_trade_calendar_endpoint(
    request: TradeCalendarSyncRequest = Body(default_factory=TradeCalendarSyncRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    today = datetime.now(tz=UTC).date()
    start = request.start_date or today - timedelta(days=370)
    end = request.end_date or today + timedelta(days=40)
    try:
        return await sync_trade_calendar(session, start, end)
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/sync/kline")
async def sync_kline_endpoint(
    request: KlineSyncRequest = Body(default_factory=KlineSyncRequest),
) -> dict:
    task_id = uuid4().hex
    payload: dict = {
        "ts_codes": request.ts_codes,
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "end_date": request.end_date.isoformat() if request.end_date else None,
    }
    async with async_session_factory() as session:
        await create_pending_task_run(
            session,
            task_name="sync_sample_kline",
            task_id=task_id,
            payload=payload,
        )
    try:
        sync_sample_kline.apply_async(kwargs=payload, task_id=task_id)
    except OperationalError as exc:
        async with async_session_factory() as session:
            await mark_task_run_queue_failed(session, task_id=task_id, error_message=f"task queue unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc
    return {"task_id": task_id, "status": "pending"}


@router.get("/sync/kline/result/{task_id}")
async def sync_kline_result(task_id: str) -> dict:
    async_result = AsyncResult(task_id, app=celery_app)
    result: dict = {
        "task_id": task_id,
        "status": async_result.status.lower(),
        "ready": async_result.ready(),
    }
    if async_result.ready():
        if async_result.failed() or async_result.status.lower() == "revoked":
            result["error"] = str(async_result.result)
        else:
            task_result = async_result.result
            result["result"] = task_result if isinstance(task_result, dict) else str(task_result)
    return result
