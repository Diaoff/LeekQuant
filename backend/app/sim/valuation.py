"""Realtime valuation enrichment for accounts and positions."""

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

from app.sim.accounts import get_account_or_404, list_accounts

async def _realtime_ticks(ts_codes: list[str]) -> tuple[dict[str, RealtimeTick], str | None]:
    if not ts_codes:
        return {}, None
    try:
        ticks = await EastMoneyRealtimeProvider(ts_codes).fetch_snapshot()
    except DataProviderError as exc:
        logger.debug("silent except in _realtime_ticks (exc): %s", exc)
        return {}, str(exc)
    return {tick.ts_code: tick for tick in ticks}, None

def _apply_realtime_position_values(
    positions: list[dict[str, Any]],
    ticks: dict[str, RealtimeTick],
) -> tuple[list[dict[str, Any]], Decimal, Decimal, bool]:
    enriched: list[dict[str, Any]] = []
    total_value = Decimal("0")
    total_unrealized_pnl = Decimal("0")
    has_realtime = False
    for position in positions:
        row = dict(position)
        ts_code = str(row["ts_code"])
        row["stock_name"] = row.get("stock_name") or ts_code
        tick = ticks.get(ts_code)
        price = tick.price if tick is not None else None
        if price is not None:
            if bool(row.get("closed_today")):
                row["current_price"] = price
                row["valuation_source"] = "realtime"
                has_realtime = True
                total_value += _dec(row.get("market_value"))
                total_unrealized_pnl += _dec(row.get("unrealized_pnl"))
                enriched.append(row)
                continue
            shares = int(row.get("shares") or 0)
            avg_cost = _dec(row.get("avg_cost"))
            market_value = _money(price * shares)
            row["current_price"] = price
            row["market_value"] = market_value
            row["unrealized_pnl"] = _money((price - avg_cost) * shares)
            row["profit_rate"] = ((price - avg_cost) / avg_cost).quantize(RATIO_QUANT) if avg_cost > 0 else Decimal("0")
            row["valuation_source"] = "realtime"
            has_realtime = True
        else:
            row["valuation_source"] = "stored"
        total_value += _dec(row.get("market_value"))
        total_unrealized_pnl += _dec(row.get("unrealized_pnl"))
        enriched.append(row)
    return enriched, _money(total_value), _money(total_unrealized_pnl), has_realtime

def _position_quote_codes(positions: list[dict[str, Any]]) -> list[str]:
    return [
        str(position["ts_code"])
        for position in positions
        if int(position.get("shares") or 0) > 0 or bool(position.get("closed_today"))
    ]

async def _position_today_baselines(session: AsyncSession, ts_codes: list[str]) -> dict[str, Decimal]:
    codes = sorted(set(ts_codes))
    if not codes:
        return {}
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (ts_code) ts_code, COALESCE(pre_close, close) AS baseline_price
            FROM daily_kline
            WHERE ts_code = ANY(:ts_codes)
              AND close IS NOT NULL
            ORDER BY ts_code, trade_date DESC
            """
        ),
        {"ts_codes": codes},
    )
    return {str(row["ts_code"]): _dec(row["baseline_price"]) for row in result.mappings().all() if row.get("baseline_price") is not None}

def _apply_position_today_pnl(
    positions: list[dict[str, Any]],
    baseline_prices: dict[str, Decimal],
    ticks: dict[str, RealtimeTick],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for position in positions:
        row = dict(position)
        ts_code = str(row["ts_code"])
        shares = int(row.get("shares") or 0)
        tick = ticks.get(ts_code)
        if tick is not None:
            row["today_pnl"] = _money(tick.change * shares)
            row["today_pnl_rate"] = format((tick.change_pct / Decimal("100")).quantize(RATIO_QUANT), "f")
            enriched.append(row)
            continue
        current_price = _dec(row.get("current_price")) if row.get("current_price") is not None else None
        previous_close = baseline_prices.get(ts_code)
        if shares > 0 and current_price is not None and previous_close is not None and previous_close > 0:
            row["today_pnl"] = _money((current_price - previous_close) * shares)
            today_pnl_rate = ((current_price - previous_close) / previous_close).quantize(RATIO_QUANT)
            row["today_pnl_rate"] = format(today_pnl_rate, "f")
        else:
            row["today_pnl"] = _money(Decimal("0"))
            row["today_pnl_rate"] = "0.00000000"
        enriched.append(row)
    return enriched

async def _latest_nav_total_asset(session: AsyncSession, account_id: int) -> Decimal | None:
    result = await session.execute(
        text(
            """
            SELECT total_asset
            FROM sim_daily_nav
            WHERE account_id = :account_id
            ORDER BY nav_date DESC
            LIMIT 1
            """
        ),
        {"account_id": account_id},
    )
    row = result.mappings().one_or_none()
    return _dec(row["total_asset"]) if row else None

async def _position_rows(session: AsyncSession, account_id: int, *, limit: int | None = None) -> list[dict[str, Any]]:
    limit_clause = "LIMIT :limit" if limit is not None else ""
    params: dict[str, Any] = {"account_id": account_id}
    if limit is not None:
        params["limit"] = limit
    result = await session.execute(
        text(
            f"""
            WITH today_traded AS (
                SELECT st.account_id,
                       st.ts_code,
                       MAX(st.id) AS trade_id,
                       SUM(CASE WHEN st.direction = '卖出' THEN st.volume ELSE 0 END)::INTEGER AS sold_shares,
                       SUM(CASE WHEN st.direction = '卖出' THEN st.amount ELSE 0 END) AS sell_amount,
                       SUM(CASE WHEN st.direction = '卖出' THEN st.total_fee ELSE 0 END) AS sell_fee,
                       MAX(st.trade_time) AS updated_at
                FROM sim_trades st
                WHERE st.account_id = :account_id
                  AND st.trade_time::DATE = CURRENT_DATE
                GROUP BY st.account_id, st.ts_code
            ),
            current_positions AS (
                SELECT p.id, p.account_id, p.ts_code, p.shares, p.available_shares, p.frozen_shares,
                       p.avg_cost, p.current_price, p.market_value, p.unrealized_pnl, p.profit_rate,
                       p.first_buy_date, p.updated_at, (p.shares = 0 AND tt.sold_shares > 0) AS closed_today
                FROM sim_positions p
                LEFT JOIN today_traded tt
                  ON tt.account_id = p.account_id AND tt.ts_code = p.ts_code
                WHERE p.account_id = :account_id
                  AND (p.shares > 0 OR tt.sold_shares > 0)
            )
            SELECT pr.id, pr.account_id, pr.ts_code, COALESCE(sb.name, pr.ts_code) AS stock_name,
                   pr.shares, pr.available_shares, pr.frozen_shares, pr.avg_cost,
                   pr.current_price, pr.market_value, pr.unrealized_pnl, pr.profit_rate,
                   pr.first_buy_date, pr.updated_at, pr.closed_today
            FROM current_positions pr
            LEFT JOIN stock_basic sb ON sb.ts_code = pr.ts_code
            ORDER BY pr.updated_at DESC, pr.id DESC
            {limit_clause}
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]

async def _account_positions(session: AsyncSession, account_id: int) -> list[dict[str, Any]]:
    return await _position_rows(session, account_id)

async def enrich_account_with_realtime_valuation(
    session: AsyncSession,
    account: dict[str, Any],
) -> dict[str, Any]:
    positions = await _account_positions(session, int(account["id"]))
    quote_codes = _position_quote_codes(positions)
    ticks, error = await _realtime_ticks(quote_codes)
    enriched_positions, position_value, unrealized_pnl, has_realtime = _apply_realtime_position_values(positions, ticks)
    baseline_prices = await _position_today_baselines(session, _position_quote_codes(enriched_positions))
    enriched_positions = _apply_position_today_pnl(enriched_positions, baseline_prices, ticks)
    row = dict(account)
    row["position_value"] = position_value
    row["unrealized_pnl"] = unrealized_pnl
    row["total_asset"] = _money(_dec(row.get("available_cash")) + _dec(row.get("frozen_cash")) + position_value)
    baseline_asset = await _latest_nav_total_asset(session, int(account["id"]))
    if baseline_asset and baseline_asset > 0:
        row["today_pnl"] = _money(_dec(row["total_asset"]) - baseline_asset)
        today_pnl_rate = ((_dec(row["total_asset"]) - baseline_asset) / baseline_asset).quantize(RATIO_QUANT)
        row["today_pnl_rate"] = format(today_pnl_rate, "f")
    else:
        row["today_pnl"] = _money(Decimal("0"))
        row["today_pnl_rate"] = "0.00000000"
    row["valuation_source"] = "realtime" if has_realtime else "stored"
    row["valuation_error"] = error
    row["positions"] = serialize_rows(enriched_positions)
    return _serialize_row(row)

async def list_accounts_with_realtime_valuation(
    session: AsyncSession,
    user_id: int,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    accounts = await list_accounts(session, user_id, status_filter)
    enriched = [await enrich_account_with_realtime_valuation(session, account) for account in accounts]
    return sorted(enriched, key=lambda account: _dec(account.get("total_asset")), reverse=True)

async def get_account_with_realtime_valuation(
    session: AsyncSession,
    account_id: int,
    user_id: int,
) -> dict[str, Any]:
    account = await get_account_or_404(session, account_id, user_id)
    return await enrich_account_with_realtime_valuation(session, account)

async def list_positions_with_realtime_valuation(
    session: AsyncSession,
    *,
    user_id: int,
    account_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    await get_account_or_404(session, account_id, user_id)
    limit = min(max(limit, 1), 500)
    positions = await _position_rows(session, account_id, limit=limit)
    quote_codes = _position_quote_codes(positions)
    ticks, error = await _realtime_ticks(quote_codes)
    enriched, _position_value, _unrealized_pnl, _has_realtime = _apply_realtime_position_values(positions, ticks)
    baseline_prices = await _position_today_baselines(session, _position_quote_codes(enriched))
    enriched = _apply_position_today_pnl(enriched, baseline_prices, ticks)
    serialized = serialize_rows(enriched)
    if error:
        for row in serialized:
            row["valuation_error"] = error
    return serialized
