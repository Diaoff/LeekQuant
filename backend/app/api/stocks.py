from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.stock_service import StockFilters, get_klines, list_stocks
from app.db.session import get_session

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


class StockListResponse(BaseModel):
    items: list[dict[str, Any]]
    page: int
    page_size: int
    total: int


@router.get("", response_model=StockListResponse)
async def stocks(
    query: str | None = None,
    exchange: str | None = None,
    industry: str | None = None,
    exclude_st: bool = False,
    exclude_delisted: bool = True,
    pe_min: Decimal | None = None,
    pe_max: Decimal | None = None,
    pb_min: Decimal | None = None,
    pb_max: Decimal | None = None,
    market_cap_min: Decimal | None = None,
    market_cap_max: Decimal | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = StockFilters(
        query=query,
        exchange=exchange,
        industry=industry,
        exclude_st=exclude_st,
        exclude_delisted=exclude_delisted,
        pe_min=pe_min,
        pe_max=pe_max,
        pb_min=pb_min,
        pb_max=pb_max,
        market_cap_min=market_cap_min,
        market_cap_max=market_cap_max,
    )
    return await list_stocks(session, filters, page=page, page_size=page_size)


@router.get("/{ts_code}/klines")
async def stock_klines(
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_date must be before end_date")
    return {"items": await get_klines(session, ts_code, start_date, end_date)}
