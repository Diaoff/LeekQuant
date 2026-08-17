"""Daily NAV refresh, T+1 unlock, stop-condition checks, position market values."""

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

from app.sim._helpers import _get_trade_calendar

async def refresh_account_assets(session: AsyncSession, *, account_id: int) -> None:
    await session.execute(
        text(
            """
            UPDATE sim_accounts
            SET total_asset = available_cash + frozen_cash + COALESCE((
                    SELECT SUM(market_value) FROM sim_positions
                    WHERE account_id = :account_id AND shares > 0
                ), 0),
                updated_at = NOW()
            WHERE id = :account_id
            """
        ),
        {"account_id": account_id},
    )

async def refresh_position_market_values(session: AsyncSession, *, account_id: int, nav_date: date) -> int:
    result = await session.execute(
        text(
            """
            UPDATE sim_positions p
            SET current_price = dk.close,
                market_value = p.shares * dk.close,
                unrealized_pnl = (dk.close - p.avg_cost) * p.shares,
                profit_rate = CASE
                    WHEN p.avg_cost <= 0 THEN 0
                    ELSE (dk.close - p.avg_cost) / p.avg_cost
                END,
                updated_at = NOW()
            FROM daily_kline dk
            WHERE p.account_id = :account_id
              AND p.shares > 0
              AND dk.ts_code = p.ts_code
              AND dk.trade_date = :nav_date
              AND dk.close IS NOT NULL
            """
        ),
        {"account_id": account_id, "nav_date": nav_date},
    )
    return int(result.rowcount or 0)

async def check_stop_conditions(session: AsyncSession, *, account_id: int, nav_date: date) -> list[dict[str, Any]]:
    account = await session.execute(
        text("SELECT config, strategy_id FROM sim_accounts WHERE id = :account_id"),
        {"account_id": account_id},
    )
    row = account.mappings().one_or_none()
    if row is None:
        return []
    cfg = row["config"] or {}
    risk_cfg = cfg.get("risk_config", {}) if isinstance(cfg, dict) else {}
    if isinstance(risk_cfg, str):
        risk_cfg = {}
    stop_loss = float(risk_cfg.get("stop_loss_pct", 0))
    take_profit = float(risk_cfg.get("take_profit_pct", 0))
    trailing_stop = float(risk_cfg.get("trailing_stop_pct", 0))
    trailing_activation = float(risk_cfg.get("trailing_activation_pct", 0))
    time_stop = int(risk_cfg.get("time_stop_days", 0))

    if not any([stop_loss, take_profit, trailing_stop, time_stop]):
        return []

    positions = await session.execute(
        text("""
            SELECT p.ts_code, p.avg_cost, p.current_price, p.shares, p.profit_rate,
                   p.first_buy_date
            FROM sim_positions p
            WHERE p.account_id = :account_id AND p.shares > 0 AND p.current_price IS NOT NULL
        """),
        {"account_id": account_id},
    )
    triggered: list[dict[str, Any]] = []
    for pos in positions.mappings().all():
        price = float(pos["current_price"])
        cost = float(pos["avg_cost"])
        if cost <= 0:
            continue
        profit_pct = float(pos["profit_rate"])
        exit_reason: str | None = None

        if stop_loss > 0 and profit_pct <= -stop_loss:
            exit_reason = "止损"
        elif take_profit > 0 and profit_pct >= take_profit:
            exit_reason = "止盈"
        elif trailing_stop > 0 and trailing_activation > 0:
            high = await session.execute(
                text("""
                    SELECT MAX(dk.high) FROM daily_kline dk
                    JOIN sim_trades st ON st.ts_code = dk.ts_code
                    WHERE st.account_id = :account_id AND st.ts_code = :ts_code
                      AND st.direction = '买入' AND dk.trade_date >= st.trade_time::DATE
                      AND dk.trade_date <= :nav_date
                """),
                {"account_id": account_id, "ts_code": pos["ts_code"], "nav_date": nav_date},
            )
            highest = float(high.scalar() or cost)
            activated = (highest - cost) / cost >= trailing_activation
            if activated:
                drawdown = (highest - price) / highest
                if drawdown >= trailing_stop:
                    exit_reason = "移动止盈"
        elif time_stop > 0 and pos["first_buy_date"]:
            import datetime
            days_held = (nav_date - pos["first_buy_date"]).days
            if days_held >= time_stop and profit_pct <= 0:
                exit_reason = "时间止损"

        if exit_reason:
            triggered.append({
                "ts_code": pos["ts_code"],
                "exit_reason": exit_reason,
                "shares": pos["shares"],
            })
    return triggered

async def unlock_t1_positions(session: AsyncSession, *, trade_date: date) -> int:
    calendar = await _get_trade_calendar(session, trade_date)
    if calendar and not calendar["is_open"]:
        return 0
    if not calendar:
        logger.warning(
            "trade_calendar missing date %s — proceeding anyway (today_buys will be empty, unlocking all eligible positions)",
            trade_date,
        )
    result = await session.execute(
        text(
            """
            WITH today_buys AS (
                SELECT account_id, ts_code, SUM(volume)::INTEGER AS volume
                FROM sim_trades
                WHERE direction = '买入' AND trade_time::DATE = :trade_date
                GROUP BY account_id, ts_code
            ),
            sellable AS (
                SELECT p.id,
                       GREATEST(0, p.shares - p.frozen_shares - COALESCE(tb.volume, 0))::INTEGER AS expected_available
                FROM sim_positions p
                LEFT JOIN today_buys tb
                  ON tb.account_id = p.account_id AND tb.ts_code = p.ts_code
                WHERE p.shares > 0
            )
            UPDATE sim_positions p
            SET available_shares = s.expected_available,
                updated_at = NOW()
            FROM sellable s
            WHERE p.id = s.id
              AND p.available_shares <> s.expected_available
            """
        ),
        {"trade_date": trade_date},
    )
    await session.commit()
    return int(result.rowcount or 0)


async def snapshot_daily_nav(session: AsyncSession, *, account_id: int, nav_date: date) -> dict[str, Any]:
    await refresh_position_market_values(session, account_id=account_id, nav_date=nav_date)

    account_info = await session.execute(
        text("SELECT user_id FROM sim_accounts WHERE id = :account_id"),
        {"account_id": account_id},
    )
    account_row = account_info.mappings().one_or_none()
    if account_row is None:
        return {"error": "account not found"}
    user_id = account_row["user_id"]

    stopped = await check_stop_conditions(session, account_id=account_id, nav_date=nav_date)
    for s in stopped:
        order_signal = SignalOrderRequest(
            ts_code=s["ts_code"],
            signal_type="卖出",
            trade_date=nav_date,
        )
        await generate_order_from_signal(
            session,
            user_id=user_id,
            account_id=account_id,
            request=order_signal,
            exit_reason_override=s["exit_reason"],
        )

    await refresh_account_assets(session, account_id=account_id)
    account_result = await session.execute(
        text("SELECT * FROM sim_accounts WHERE id = :account_id"),
        {"account_id": account_id},
    )
    account = dict(account_result.mappings().one())
    pos_result = await session.execute(
        text("SELECT COALESCE(SUM(market_value), 0) FROM sim_positions WHERE account_id = :account_id AND shares > 0"),
        {"account_id": account_id},
    )
    position_value = _dec(pos_result.scalar_one())
    prev_result = await session.execute(
        text(
            """
            SELECT total_asset, cumulative_nav, max_drawdown
            FROM sim_daily_nav
            WHERE account_id = :account_id AND nav_date < :nav_date
            ORDER BY nav_date DESC
            LIMIT 1
            """
        ),
        {"account_id": account_id, "nav_date": nav_date},
    )
    prev = prev_result.mappings().one_or_none()
    total_asset = _dec(account["total_asset"])
    if prev:
        prev_asset = _dec(prev["total_asset"])
        prev_nav = _dec(prev["cumulative_nav"])
        daily_return = (total_asset / prev_asset - Decimal("1")) if prev_asset > 0 else Decimal("0")
        cumulative_nav = prev_nav * (Decimal("1") + daily_return)
        max_drawdown = min(_dec(prev["max_drawdown"]), cumulative_nav - Decimal("1"))
    else:
        daily_return = Decimal("0")
        cumulative_nav = Decimal("1")
        max_drawdown = Decimal("0")

    result = await session.execute(
        text(
            """
            INSERT INTO sim_daily_nav (
                account_id, nav_date, total_asset, available_cash, frozen_cash,
                position_value, daily_return, cumulative_nav, max_drawdown
            ) VALUES (
                :account_id, :nav_date, :total_asset, :available_cash, :frozen_cash,
                :position_value, :daily_return, :cumulative_nav, :max_drawdown
            )
            ON CONFLICT (account_id, nav_date) DO UPDATE SET
                total_asset = EXCLUDED.total_asset,
                available_cash = EXCLUDED.available_cash,
                frozen_cash = EXCLUDED.frozen_cash,
                position_value = EXCLUDED.position_value,
                daily_return = EXCLUDED.daily_return,
                cumulative_nav = EXCLUDED.cumulative_nav,
                max_drawdown = EXCLUDED.max_drawdown
            RETURNING *
            """
        ),
        {
            "account_id": account_id,
            "nav_date": nav_date,
            "total_asset": total_asset,
            "available_cash": _dec(account["available_cash"]),
            "frozen_cash": _dec(account["frozen_cash"]),
            "position_value": position_value,
            "daily_return": daily_return.quantize(Decimal("0.00000001")),
            "cumulative_nav": cumulative_nav.quantize(Decimal("0.00000001")),
            "max_drawdown": max_drawdown.quantize(Decimal("0.00000001")),
        },
    )
    row = dict(result.mappings().one())
    await session.commit()
    return _serialize_row(row)
