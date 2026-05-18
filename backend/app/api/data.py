from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.service import default_kline_window, get_data_status, sync_kline, sync_stock_basic, sync_trade_calendar
from app.db.session import get_session

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
    session: AsyncSession = Depends(get_session),
) -> dict:
    default_start, default_end = default_kline_window()
    start = request.start_date or default_start
    end = request.end_date or default_end
    try:
        return await sync_kline(session, request.ts_codes, start, end)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
