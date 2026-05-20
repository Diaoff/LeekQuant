from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.stock_service import (
    add_watchlist_item,
    create_watchlist_group,
    delete_watchlist_group,
    delete_watchlist_item,
    list_watchlist,
    list_watchlist_groups,
    rename_watchlist_group,
    update_watchlist_item,
)
from app.db.session import get_session

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistCreateRequest(BaseModel):
    ts_code: str
    group_name: str = Field(default="默认", max_length=64)
    note: str | None = None
    sort_order: int = 0


class WatchlistUpdateRequest(BaseModel):
    group_name: str | None = Field(default=None, max_length=64)
    note: str | None = None
    sort_order: int | None = None


class WatchlistGroupCreateRequest(BaseModel):
    group_name: str = Field(min_length=1, max_length=64)


class WatchlistGroupUpdateRequest(BaseModel):
    group_name: str = Field(min_length=1, max_length=64)


@router.get("")
async def get_watchlist(
    group_name: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_watchlist(session, group_name=group_name)


@router.post("")
async def add_watchlist(
    request: WatchlistCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        return await add_watchlist_item(
            session,
            ts_code=request.ts_code,
            group_name=request.group_name,
            note=request.note,
            sort_order=request.sort_order,
        )
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/{item_id}")
async def patch_watchlist(
    item_id: int,
    request: WatchlistUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    item = await update_watchlist_item(
        session,
        item_id,
        group_name=request.group_name,
        note=request.note,
        sort_order=request.sort_order,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="watchlist item not found")
    return item


@router.get("/groups")
async def get_watchlist_groups(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await list_watchlist_groups(session)


@router.post("/groups")
async def post_watchlist_group(
    request: WatchlistGroupCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        return await create_watchlist_group(session, request.group_name)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/groups/{group_name}")
async def patch_watchlist_group(
    group_name: str,
    request: WatchlistGroupUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        item = await rename_watchlist_group(session, group_name, request.group_name)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="watchlist group not found")
    return item


@router.delete("/groups/{group_name}")
async def delete_watchlist_group_endpoint(
    group_name: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    try:
        deleted = await delete_watchlist_group(session, group_name)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="watchlist group not found")
    return {"deleted": True}


@router.delete("/{item_id}")
async def delete_watchlist(item_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    deleted = await delete_watchlist_item(session, item_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="watchlist item not found")
    return {"deleted": True}


@router.get("/summary")
async def get_watchlist_summary(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return watchlist overview for dashboard: total count and today's gainers/losers."""
    result = await session.execute(
        text(
            """
            SELECT
                COUNT(DISTINCT w.id) AS total_count,
                COALESCE(SUM(CASE WHEN dk.close > dk.pre_close THEN 1 ELSE 0 END), 0) AS today_gainers,
                COALESCE(SUM(CASE WHEN dk.close < dk.pre_close THEN 1 ELSE 0 END), 0) AS today_losers,
                COALESCE(SUM(CASE WHEN dk.close = dk.pre_close THEN 1 ELSE 0 END), 0) AS today_flat
            FROM watchlist w
            LEFT JOIN daily_kline dk ON dk.ts_code = w.ts_code
                AND dk.trade_date = (
                    SELECT MAX(trade_date) FROM daily_kline dk2 WHERE dk2.ts_code = w.ts_code
                )
            """
        ),
    )
    row = result.mappings().first()
    return {
        "total_count": row["total_count"] if row else 0,
        "today_gainers": row["today_gainers"] if row else 0,
        "today_losers": row["today_losers"] if row else 0,
        "today_flat": row["today_flat"] if row else 0,
    }
