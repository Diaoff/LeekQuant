"""Factor definition, ranking, values, and IC/IR query API."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.factor.service import (
    list_factor_definitions,
    query_factor_analysis,
    query_factor_values,
    query_rank,
)

router = APIRouter(prefix="/api/factors", tags=["factors"])


@router.get("")
async def get_factors(
    enabled_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_factor_definitions(session, enabled_only=enabled_only)


@router.get("/rank")
async def get_factor_rank(
    trade_date: date | None = None,
    scope_type: str = Query(default="all", pattern="^(all|watchlist_group)$"),
    scope_value: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if scope_type == "watchlist_group" and not scope_value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scope_value is required for watchlist_group scope",
        )
    return await query_rank(
        session,
        trade_date=trade_date,
        scope_type=scope_type,
        scope_value=scope_value,
        page=page,
        page_size=page_size,
    )


@router.get("/analysis")
async def get_factor_analysis(
    factor_name: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await query_factor_analysis(
        session,
        factor_name=factor_name,
        page=page,
        page_size=page_size,
    )


@router.get("/values")
async def get_factor_values(
    trade_date: date,
    factor_name: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await query_factor_values(
        session,
        trade_date=trade_date,
        factor_name=factor_name,
        page=page,
        page_size=page_size,
    )
