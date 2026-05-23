from __future__ import annotations

from datetime import date
from uuid import uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, Body, Depends, HTTPException, status
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repository import create_pending_task_run, mark_task_run_queue_failed
from app.db.session import get_session
from app.tasks.celery_app import celery_app
from app.tasks.data_tasks import sync_all_kline, sync_fundamentals_task, sync_sample_kline
from app.tasks.factor_tasks import analyze_factor_icir_task, compute_daily_factors

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class SampleKlineTaskRequest(BaseModel):
    ts_codes: list[str] | None = Field(default=None, max_length=30)
    start_date: date | None = None
    end_date: date | None = None


class FundamentalsTaskRequest(BaseModel):
    ts_codes: list[str] | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None


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


@router.post("/data/sample-kline")
async def start_sample_kline_task(
    request: SampleKlineTaskRequest = Body(default_factory=SampleKlineTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    task_id = uuid4().hex
    payload = {
        "ts_codes": request.ts_codes,
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "end_date": request.end_date.isoformat() if request.end_date else None,
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
    request: SampleKlineTaskRequest = Body(default_factory=SampleKlineTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    task_id = uuid4().hex
    payload = {
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "end_date": request.end_date.isoformat() if request.end_date else None,
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


@router.post("/data/fundamentals")
async def start_fundamentals_task(
    request: FundamentalsTaskRequest = Body(default_factory=FundamentalsTaskRequest),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    task_id = uuid4().hex
    payload = {
        "ts_codes": request.ts_codes,
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "end_date": request.end_date.isoformat() if request.end_date else None,
    }
    await create_pending_task_run(
        session,
        task_name="sync_fundamentals",
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
    payload = {
        "task_id": task_id,
        "status": async_result.status.lower(),
        "ready": async_result.ready(),
    }
    info = async_result.info
    if isinstance(info, dict) and "current" in info:
        payload["meta"] = info
    if async_result.ready():
        if async_result.failed():
            payload["error"] = str(async_result.result)
        else:
            payload["result"] = async_result.result
    return payload
