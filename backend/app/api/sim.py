"""Simulation trading API endpoints."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.sim.service import (
    SignalOrderRequest,
    cancel_order,
    create_account,
    delete_account,
    generate_order_from_signal,
    get_account_or_404,
    get_account_with_realtime_valuation,
    list_accounts,
    list_accounts_with_realtime_valuation,
    list_child_rows,
    list_positions_with_realtime_valuation,
    match_order,
    serialize_rows,
    update_account,
)

router = APIRouter(prefix="/api/sim", tags=["simulation"])


def _extract_user_id(request: Request) -> int:
    raw = request.headers.get("X-User-ID") or request.query_params.get("user_id")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 1
    return 1


class AccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    initial_cash: Decimal = Field(gt=0)
    strategy_id: int | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class AccountUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    strategy_id: int | None = None
    config: dict[str, Any] | None = None


class SignalCreateRequest(BaseModel):
    ts_code: str = Field(min_length=1, max_length=10)
    signal_type: str
    trade_date: date
    strategy_id: int | None = None
    target_position: Decimal | None = Field(default=None, ge=0, le=1)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    reason: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)


class MatchRequest(BaseModel):
    trade_date: date | None = None
    match_mode: str = Field(default="close", pattern="^(close|open|limit)$")


@router.get("/accounts")
async def get_accounts(
    req: Request,
    status_filter: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_accounts_with_realtime_valuation(session, _extract_user_id(req), status_filter)


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account_endpoint(
    request: AccountCreateRequest,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await create_account(
        session,
        user_id=_extract_user_id(req),
        name=request.name,
        initial_cash=request.initial_cash,
        strategy_id=request.strategy_id,
        config=request.config,
    )


@router.get("/accounts/{account_id}")
async def get_account_detail(
    account_id: int,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await get_account_with_realtime_valuation(session, account_id, _extract_user_id(req))


@router.patch("/accounts/{account_id}")
async def patch_account(
    account_id: int,
    request: AccountUpdateRequest,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await update_account(
        session,
        account_id=account_id,
        user_id=_extract_user_id(req),
        name=request.name,
        strategy_id=request.strategy_id,
        strategy_id_provided="strategy_id" in request.model_fields_set,
        config=request.config,
    )


@router.delete("/accounts/{account_id}")
async def delete_account_endpoint(
    account_id: int,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    deleted = await delete_account(session, account_id=account_id, user_id=_extract_user_id(req))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return {"deleted": True}


@router.get("/accounts/{account_id}/positions")
async def get_positions(
    account_id: int,
    req: Request,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_positions_with_realtime_valuation(
        session,
        user_id=_extract_user_id(req),
        account_id=account_id,
        limit=limit,
    )


@router.get("/accounts/{account_id}/orders")
async def get_orders(
    account_id: int,
    req: Request,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_child_rows(
        session,
        user_id=_extract_user_id(req),
        account_id=account_id,
        table="sim_orders",
        order_by="submit_time DESC, id DESC",
        limit=limit,
    )


@router.get("/accounts/{account_id}/trades")
async def get_trades(
    account_id: int,
    req: Request,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_child_rows(
        session,
        user_id=_extract_user_id(req),
        account_id=account_id,
        table="sim_trades",
        order_by="trade_time DESC, id DESC",
        limit=limit,
    )


@router.get("/accounts/{account_id}/cash-flow")
async def get_cash_flow(
    account_id: int,
    req: Request,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_child_rows(
        session,
        user_id=_extract_user_id(req),
        account_id=account_id,
        table="sim_cash_flow",
        order_by="created_at DESC, id DESC",
        limit=limit,
    )


@router.get("/accounts/{account_id}/nav")
async def get_nav(
    account_id: int,
    req: Request,
    limit: int = Query(default=120, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_child_rows(
        session,
        user_id=_extract_user_id(req),
        account_id=account_id,
        table="sim_daily_nav",
        order_by="nav_date DESC",
        limit=limit,
    )


@router.post("/accounts/{account_id}/signals", status_code=status.HTTP_201_CREATED)
async def create_signal_order(
    account_id: int,
    request: SignalCreateRequest,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await generate_order_from_signal(
        session,
        user_id=_extract_user_id(req),
        account_id=account_id,
        request=SignalOrderRequest(
            ts_code=request.ts_code.strip().upper(),
            signal_type=request.signal_type,
            trade_date=request.trade_date,
            strategy_id=request.strategy_id,
            target_position=request.target_position,
            confidence=request.confidence,
            reason=request.reason,
            snapshot=request.snapshot,
        ),
        auto_match=True,
        auto_match_mode="close",
    )


@router.post("/orders/{order_id}/match")
async def match_order_endpoint(
    order_id: int,
    request: MatchRequest,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await match_order(
        session,
        user_id=_extract_user_id(req),
        order_id=order_id,
        trade_date=request.trade_date,
        match_mode=request.match_mode,
    )


@router.post("/orders/{order_id}/cancel")
async def cancel_order_endpoint(
    order_id: int,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await cancel_order(session, user_id=_extract_user_id(req), order_id=order_id)
