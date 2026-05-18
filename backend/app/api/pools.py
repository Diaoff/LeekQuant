from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.stock_service import (
    create_pool,
    delete_pool,
    get_pool,
    list_pool_items,
    list_pools,
    rebuild_pool,
    update_pool,
)
from app.db.session import get_session

router = APIRouter(prefix="/api/pools", tags=["pools"])


class PoolCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    is_dynamic: bool = True


class PoolUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    filters: dict[str, Any] | None = None
    is_dynamic: bool | None = None


@router.get("")
async def pools(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await list_pools(session)


@router.post("")
async def create_stock_pool(
    request: PoolCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await create_pool(
        session,
        name=request.name,
        description=request.description,
        filters=request.filters,
        is_dynamic=request.is_dynamic,
    )


@router.get("/{pool_id}")
async def stock_pool(pool_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    pool = await get_pool(session, pool_id)
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pool not found")
    return pool


@router.patch("/{pool_id}")
async def patch_stock_pool(
    pool_id: int,
    request: PoolUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    pool = await update_pool(
        session,
        pool_id,
        name=request.name,
        description=request.description,
        filters=request.filters,
        is_dynamic=request.is_dynamic,
    )
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pool not found")
    return pool


@router.delete("/{pool_id}")
async def delete_stock_pool(pool_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    deleted = await delete_pool(session, pool_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pool not found")
    return {"deleted": True}


@router.post("/{pool_id}/rebuild")
async def rebuild_stock_pool(pool_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await rebuild_pool(session, pool_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pool not found")
    return result


@router.get("/{pool_id}/items")
async def stock_pool_items(pool_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    items = await list_pool_items(session, pool_id)
    if items is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pool not found")
    return {"items": items}
