"""Leaf helpers for simulation: fee config + kline/trade-calendar data access."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.cost import AShareCostCalculator, FeeConfig, build_fee_config
from app.backtest.signals import SignalInput, apply_cn_rules, map_signal_to_action
from app.data.providers import DataProviderError
from app.realtime.models import RealtimeTick
from app.realtime.providers import EastMoneyRealtimeProvider
from app.sim.serialize import (
    MONEY_QUANT,
    _dec,
    _dict_value,
    _json,
    _money,
    _serialize_row,
    serialize_rows,
)

logger = logging.getLogger(__name__)

LOT_SIZE = 100
RATIO_QUANT = Decimal("0.00000001")

@dataclass(slots=True)
class SignalOrderRequest:
    ts_code: str
    signal_type: str
    trade_date: date
    strategy_id: int | None = None
    target_position: Decimal | None = None
    confidence: Decimal | None = None
    reason: str | None = None
    snapshot: dict[str, Any] | None = None

def _fee_config(config: dict[str, Any] | None, global_config: FeeConfig | None = None) -> FeeConfig:
    fee_cfg = (config or {}).get("fee_config") if isinstance(config, dict) else None
    global_cfg = (
        {
            "commission_rate": global_config.commission_rate,
            "min_commission": global_config.min_commission,
            "stamp_tax_rate": global_config.stamp_tax_rate,
            "transfer_fee_rate": global_config.transfer_fee_rate,
            "waive_min_commission": global_config.waive_min_commission,
        }
        if global_config
        else None
    )
    return build_fee_config(global_cfg, fee_cfg if isinstance(fee_cfg, dict) else None)

def _global_fee_config(row: dict[str, Any]) -> FeeConfig:
    return build_fee_config(_dict_value(row.get("user_trading_fee_config")))

async def _get_trade_calendar(session: AsyncSession, trade_date: date) -> dict[str, Any] | None:
    result = await session.execute(
        text("SELECT cal_date, is_open, pretrade_date, nexttrade_date FROM trade_calendar WHERE cal_date = :trade_date"),
        {"trade_date": trade_date},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None

async def _get_kline(session: AsyncSession, ts_code: str, trade_date: date) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT dk.ts_code, dk.trade_date, dk.open, dk.high, dk.low, dk.close,
                   dk.pre_close, dk.is_suspended, dk.is_limit_up, dk.is_limit_down,
                   sb.is_st, sb.market
            FROM daily_kline dk
            LEFT JOIN stock_basic sb ON sb.ts_code = dk.ts_code
            WHERE dk.ts_code = :ts_code AND dk.trade_date = :trade_date
            """
        ),
        {"ts_code": ts_code, "trade_date": trade_date},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None

async def _get_latest_kline_before_or_on(session: AsyncSession, ts_code: str, trade_date: date) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT dk.ts_code, dk.trade_date, dk.open, dk.high, dk.low, dk.close,
                   dk.pre_close, dk.is_suspended, dk.is_limit_up, dk.is_limit_down,
                   sb.is_st, sb.market
            FROM daily_kline dk
            LEFT JOIN stock_basic sb ON sb.ts_code = dk.ts_code
            WHERE dk.ts_code = :ts_code
              AND dk.trade_date <= :trade_date
              AND dk.close IS NOT NULL
            ORDER BY dk.trade_date DESC
            LIMIT 1
            """
        ),
        {"ts_code": ts_code, "trade_date": trade_date},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None

async def _get_position(
    session: AsyncSession,
    account_id: int,
    ts_code: str,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if for_update else ""
    result = await session.execute(
        text(f"SELECT * FROM sim_positions WHERE account_id = :account_id AND ts_code = :ts_code{suffix}"),
        {"account_id": account_id, "ts_code": ts_code},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None
