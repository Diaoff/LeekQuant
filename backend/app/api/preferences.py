"""User preference API endpoints."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.preferences.service import get_kline_sync_payload, get_trading_fee_payload, save_kline_sync_payload, save_trading_fee_payload

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


def _extract_user_id(request: Request) -> int:
    raw = request.headers.get("X-User-ID") or request.query_params.get("user_id")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 1
    return 1


class TradingFeePreference(BaseModel):
    commission_rate: Decimal = Field(default=Decimal("0.00025"), ge=0)
    min_commission: Decimal = Field(default=Decimal("5.0"), ge=0)
    waive_min_commission: bool = False
    stamp_tax_rate: Decimal = Field(default=Decimal("0.0005"), ge=0)
    transfer_fee_rate: Decimal = Field(default=Decimal("0.00001"), ge=0)


class KlineSyncPreference(BaseModel):
    full_kline_sync_concurrency: int | None = Field(default=None, ge=1, le=8)
    concurrency: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def normalize_concurrency(self) -> "KlineSyncPreference":
        if self.full_kline_sync_concurrency is None:
            self.full_kline_sync_concurrency = self.concurrency
        return self


@router.get("/trading-fee")
async def get_trading_fee(
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | bool]:
    return await get_trading_fee_payload(session, _extract_user_id(req))


@router.put("/trading-fee")
async def update_trading_fee(
    request: TradingFeePreference,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | bool]:
    return await save_trading_fee_payload(
        session,
        user_id=_extract_user_id(req),
        payload=request.model_dump(),
    )


@router.get("/kline-sync")
async def get_kline_sync(
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    return await get_kline_sync_payload(session)


@router.put("/kline-sync")
async def update_kline_sync(
    request: KlineSyncPreference,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    return await save_kline_sync_payload(
        session,
        {"full_kline_sync_concurrency": request.full_kline_sync_concurrency},
    )
