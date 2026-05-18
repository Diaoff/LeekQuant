"""Strategy CRUD API endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.strategy_service import (
    create_strategy,
    delete_strategy,
    get_strategy,
    list_strategies,
    update_strategy,
)
from app.data.stock_service import LOCAL_USER_ID
from app.db.session import get_session

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class StrategyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    source_code: str = Field(min_length=1)
    description: str | None = None
    pool_id: int | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"


class StrategyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    source_code: str | None = None
    pool_id: int | None = None
    config: dict[str, Any] | None = None
    status: str | None = None


@router.get("")
async def get_strategies(
    status_filter: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_strategies(session, LOCAL_USER_ID, status_filter)


@router.get("/{strategy_id}")
async def get_strategy_detail(
    strategy_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await get_strategy(session, strategy_id, LOCAL_USER_ID)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_strategy_endpoint(
    request: StrategyCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await create_strategy(
        session,
        name=request.name,
        source_code=request.source_code,
        description=request.description,
        pool_id=request.pool_id,
        config=request.config,
        status=request.status,
        user_id=LOCAL_USER_ID,
    )


@router.patch("/{strategy_id}")
async def patch_strategy(
    strategy_id: int,
    request: StrategyUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await update_strategy(
        session,
        strategy_id,
        name=request.name,
        description=request.description,
        source_code=request.source_code,
        pool_id=request.pool_id,
        config=request.config,
        status=request.status,
        user_id=LOCAL_USER_ID,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")
    return result


@router.delete("/{strategy_id}")
async def delete_strategy_endpoint(
    strategy_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    deleted = await delete_strategy(session, strategy_id, LOCAL_USER_ID)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")
    return {"deleted": True}
