"""Simulation trading service for A-share signal execution."""
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

logger = logging.getLogger(__name__)

LOT_SIZE = 100
MONEY_QUANT = Decimal("0.0001")
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


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    quantized = value.quantize(MONEY_QUANT)
    return Decimal("0").quantize(MONEY_QUANT) if quantized == 0 else quantized


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


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


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            payload[key] = str(value)
        elif isinstance(value, (date, datetime)):
            payload[key] = value.isoformat()
        else:
            payload[key] = value
    return payload


def serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_serialize_row(dict(row)) for row in rows]


async def _realtime_ticks(ts_codes: list[str]) -> tuple[dict[str, RealtimeTick], str | None]:
    if not ts_codes:
        return {}, None
    try:
        ticks = await EastMoneyRealtimeProvider(ts_codes).fetch_snapshot()
    except DataProviderError as exc:
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


async def get_account_or_404(
    session: AsyncSession,
    account_id: int,
    user_id: int,
    *,
    for_update: bool = False,
) -> dict[str, Any]:
    suffix = " FOR UPDATE OF a" if for_update else ""
    result = await session.execute(
        text(
            f"""
            SELECT a.id, a.user_id, a.strategy_id, a.name, a.initial_cash, a.available_cash,
                   a.frozen_cash, a.total_asset, a.status, a.config, a.created_at, a.updated_at,
                   p.value AS user_trading_fee_config
            FROM sim_accounts a
            LEFT JOIN user_preferences p
              ON p.user_id = a.user_id AND p.key = 'trading_fee'
            WHERE a.id = :id AND a.user_id = :user_id
            {suffix}
            """
        ),
        {"id": account_id, "user_id": user_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return dict(row)


async def list_accounts(session: AsyncSession, user_id: int, status_filter: str | None = None) -> list[dict[str, Any]]:
    clauses = ["a.user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id}
    if status_filter:
        clauses.append("a.status = :status")
        params["status"] = status_filter
    result = await session.execute(
        text(
            f"""
            SELECT a.id, a.user_id, a.strategy_id, a.name, a.initial_cash,
                   a.available_cash, a.frozen_cash, a.total_asset, a.status,
                   a.config, a.created_at, a.updated_at, s.name AS strategy_name
            FROM sim_accounts a
            LEFT JOIN strategies s ON s.id = a.strategy_id
            WHERE {" AND ".join(clauses)}
            ORDER BY a.total_asset DESC, a.id DESC
            """
        ),
        params,
    )
    return serialize_rows(list(result.mappings().all()))


async def create_account(
    session: AsyncSession,
    *,
    user_id: int,
    name: str,
    initial_cash: Decimal,
    strategy_id: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if initial_cash <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="initial_cash must be positive")
    result = await session.execute(
        text(
            """
            INSERT INTO sim_accounts (
                user_id, strategy_id, name, initial_cash, available_cash,
                frozen_cash, total_asset, config
            ) VALUES (
                :user_id, :strategy_id, :name, :initial_cash, :initial_cash,
                0, :initial_cash, CAST(:config AS JSONB)
            )
            RETURNING id, user_id, strategy_id, name, initial_cash, available_cash,
                      frozen_cash, total_asset, status, config, created_at, updated_at
            """
        ),
        {
            "user_id": user_id,
            "strategy_id": strategy_id,
            "name": name,
            "initial_cash": _money(initial_cash),
            "config": _json(config),
        },
    )
    row = dict(result.mappings().one())
    await session.execute(
        text(
            """
            INSERT INTO sim_cash_flow (account_id, flow_type, amount, balance_after, remark)
            VALUES (:account_id, '充值', :amount, :amount, '模拟账户初始化')
            """
        ),
        {"account_id": row["id"], "amount": _money(initial_cash)},
    )
    await session.commit()
    return _serialize_row(row)


async def update_account(
    session: AsyncSession,
    *,
    account_id: int,
    user_id: int,
    name: str | None = None,
    strategy_id: int | None = None,
    strategy_id_provided: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = await get_account_or_404(session, account_id, user_id)
    new_name = name if name is not None else existing["name"]
    new_strategy_id = strategy_id if strategy_id_provided else existing["strategy_id"]
    new_config: dict[str, Any] | None = None
    if config is not None:
        cur_config = existing.get("config")
        if isinstance(cur_config, dict):
            merged = dict(cur_config)
            merged.update(config)
            new_config = merged
        else:
            new_config = config
    result = await session.execute(
        text(
            """
            UPDATE sim_accounts
            SET name = :name, strategy_id = :strategy_id,
                config = COALESCE(:config, config),
                updated_at = NOW()
            WHERE id = :id AND user_id = :user_id
            RETURNING id, user_id, strategy_id, name, initial_cash, available_cash,
                      frozen_cash, total_asset, status, config, created_at, updated_at
            """
        ),
        {
            "name": new_name,
            "strategy_id": new_strategy_id,
            "config": json.dumps(new_config) if new_config is not None else None,
            "id": account_id,
            "user_id": user_id,
        },
    )
    row = dict(result.mappings().one())
    await session.commit()
    return _serialize_row(row)


async def delete_account(
    session: AsyncSession,
    *,
    account_id: int,
    user_id: int,
) -> bool:
    await get_account_or_404(session, account_id, user_id)
    result = await session.execute(
        text("DELETE FROM sim_accounts WHERE id = :id AND user_id = :user_id"),
        {"id": account_id, "user_id": user_id},
    )
    await session.commit()
    return bool(result.rowcount)


async def list_child_rows(
    session: AsyncSession,
    *,
    user_id: int,
    account_id: int,
    table: str,
    order_by: str,
    limit: int = 100,
    include_stock_name: bool = False,
) -> list[dict[str, Any]]:
    await get_account_or_404(session, account_id, user_id)
    limit = min(max(limit, 1), 500)
    select_clause = "t.*, COALESCE(sb.name, t.ts_code) AS stock_name" if include_stock_name else "t.*"
    join_clause = "LEFT JOIN stock_basic sb ON sb.ts_code = t.ts_code" if include_stock_name else ""
    result = await session.execute(
        text(
            f"""
            SELECT {select_clause}
            FROM {table} t
            {join_clause}
            WHERE t.account_id = :account_id
            ORDER BY {order_by}
            LIMIT :limit
            """
        ),
        {"account_id": account_id, "limit": limit},
    )
    return serialize_rows(list(result.mappings().all()))


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


def _resolve_match_price(
    order: dict[str, Any],
    kline: dict[str, Any],
    match_mode: str,
    *,
    realtime_price: Decimal | None = None,
) -> Decimal:
    if match_mode == "open":
        if kline.get("open") is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily open price not found")
        return _dec(kline["open"])
    if match_mode == "close":
        if kline.get("close") is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily close price not found")
        return _dec(kline["close"])

    order_price = _dec(order.get("price"))
    if realtime_price is not None:
        tick_price = _dec(realtime_price)
        direction = str(order.get("direction") or "")
        if direction == "买入" and order_price < tick_price:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="限价未触达")
        if direction == "卖出" and order_price > tick_price:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="限价未触达")
        return order_price

    low = _dec(kline.get("low")) if kline.get("low") is not None else None
    high = _dec(kline.get("high")) if kline.get("high") is not None else None
    if low is None or high is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily high/low not found")
    if order_price < low or order_price > high:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="限价未触达")
    return order_price


def _resolve_order_price_fallback(order: dict[str, Any]) -> Decimal:
    if order.get("price") is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily kline not found and order price is missing")
    price = _dec(order["price"])
    if price <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="order price must be positive")
    return price


def _limit_rate(kline: dict[str, Any]) -> Decimal:
    market = str(kline.get("market") or "")
    ts_code = str(kline.get("ts_code") or "")
    if bool(kline.get("is_st")):
        return Decimal("0.05")
    if market in {"科创板", "创业板"} or ts_code.startswith(("688", "300")):
        return Decimal("0.20")
    if market == "北交所" or ts_code.endswith(".BJ") or ts_code.startswith(("8", "4")):
        return Decimal("0.30")
    return Decimal("0.10")


def _computed_limit_flags(kline: dict[str, Any]) -> tuple[bool, bool]:
    if bool(kline.get("is_limit_up")) or bool(kline.get("is_limit_down")):
        return bool(kline.get("is_limit_up")), bool(kline.get("is_limit_down"))
    if kline.get("pre_close") is None or kline.get("close") is None:
        return False, False

    pre_close = _dec(kline["pre_close"])
    close = _dec(kline["close"])
    if pre_close <= 0:
        return False, False

    rate = _limit_rate(kline)
    return close >= pre_close * (Decimal("1") + rate), close <= pre_close * (Decimal("1") - rate)


async def _insert_signal(
    session: AsyncSession,
    *,
    user_id: int,
    account_id: int | None,
    request: SignalOrderRequest,
    current_position: Decimal,
    action: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    target_position = request.target_position if request.target_position is not None else _dec(snapshot.get("target_position"))
    result = await session.execute(
        text(
            """
            INSERT INTO signal_log (
                user_id, strategy_id, account_id, ts_code, trade_date, signal_type,
                target_position, current_position, action, confidence, reason, snapshot
            ) VALUES (
                :user_id, :strategy_id, :account_id, :ts_code, :trade_date, :signal_type,
                :target_position, :current_position, :action, :confidence, :reason,
                CAST(:snapshot AS JSONB)
            )
            RETURNING id, user_id, strategy_id, account_id, ts_code, trade_date,
                      signal_type, target_position, current_position, action,
                      confidence, reason, snapshot, created_at
            """
        ),
        {
            "user_id": user_id,
            "strategy_id": request.strategy_id,
            "account_id": account_id,
            "ts_code": request.ts_code,
            "trade_date": request.trade_date,
            "signal_type": request.signal_type,
            "target_position": target_position,
            "current_position": current_position,
            "action": action,
            "confidence": request.confidence,
            "reason": request.reason,
            "snapshot": _json(snapshot),
        },
    )
    return dict(result.mappings().one())


def _strategy_signal_response(strategy_signal_id: int | None) -> dict[str, Any] | None:
    return {"id": strategy_signal_id} if strategy_signal_id is not None else None


async def generate_order_from_signal(
    session: AsyncSession,
    *,
    user_id: int,
    account_id: int,
    request: SignalOrderRequest,
    exit_reason_override: str | None = None,
    order_price_override: Decimal | None = None,
    prevent_duplicate_sell_order: bool = False,
    strategy_signal_id: int | None = None,
    auto_commit: bool = True,
    allow_missing_kline_with_order_price: bool = False,
    auto_match: bool = False,
    auto_match_mode: str = "close",
) -> dict[str, Any]:
    account = await get_account_or_404(session, account_id, user_id, for_update=True)
    if account["status"] != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="account is not active")

    if exit_reason_override:
        request = replace(request, reason=exit_reason_override)

    calendar = await _get_trade_calendar(session, request.trade_date)
    position = await _get_position(session, account_id, request.ts_code, for_update=True)
    shares = int(position["shares"]) if position else 0
    market_value = _dec(position["market_value"]) if position else Decimal("0")
    total_asset = _dec(account["total_asset"])
    current_position = (market_value / total_asset) if total_asset > 0 else Decimal("0")
    state = map_signal_to_action(
        SignalInput(
            signal_type=request.signal_type,
            current_position=float(current_position),
            target_position=float(request.target_position) if request.target_position is not None else None,
        )
    )

    snapshot = dict(request.snapshot or {})
    snapshot.update(
        {
            "requested_signal": request.signal_type,
            "target_position": state.target_position,
            "current_position": str(current_position),
        }
    )

    if not calendar or not calendar["is_open"]:
        signal = _strategy_signal_response(strategy_signal_id)
        if signal is None:
            signal = await _insert_signal(
                session,
                user_id=user_id,
                account_id=account_id,
                request=request,
                current_position=current_position,
                action="BLOCKED",
                snapshot={**snapshot, "blocked_reason": "非交易日"},
            )
            signal = _serialize_row(signal)
        if auto_commit:
            await session.commit()
        return {"signal": signal, "order": None, "action": "BLOCKED", "reason": "非交易日"}

    kline = await _get_kline(session, request.ts_code, request.trade_date)
    if not kline or kline.get("close") is None:
        prev_trade_date = request.trade_date
        cal = await session.execute(
            text("SELECT pretrade_date FROM trade_calendar WHERE cal_date = :d"),
            {"d": request.trade_date},
        )
        prev_cal = cal.mappings().one_or_none()
        if prev_cal:
            prev_trade_date = prev_cal["pretrade_date"]
        kline = await _get_kline(session, request.ts_code, prev_trade_date)
    if not kline or kline.get("close") is None:
        kline = await _get_latest_kline_before_or_on(session, request.ts_code, request.trade_date)
    if not kline or kline.get("close") is None:
        if allow_missing_kline_with_order_price and order_price_override is not None:
            kline = {
                "ts_code": request.ts_code,
                "trade_date": request.trade_date,
                "close": order_price_override,
                "pre_close": None,
                "is_suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
                "is_realtime_fallback": True,
            }
            snapshot["kline_fallback"] = "realtime_order_price"
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily kline not found")

    is_limit_up, is_limit_down = _computed_limit_flags(kline)
    action, blocked_reason = apply_cn_rules(
        state.action,
        is_suspended=bool(kline.get("is_suspended")),
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
        is_t1_blocked=state.action.startswith("SELL") and shares > 0 and (not position or int(position["available_shares"]) <= 0),
        enforce_price_limits=False,
    )
    if action in ("HOLD", "BLOCKED"):
        reason = blocked_reason or state.reason or "观望"
        signal = _strategy_signal_response(strategy_signal_id)
        if signal is None:
            signal = await _insert_signal(
                session,
                user_id=user_id,
                account_id=account_id,
                request=request,
                current_position=current_position,
                action=action,
                snapshot={**snapshot, "blocked_reason": blocked_reason} if blocked_reason else snapshot,
            )
            signal = _serialize_row(signal)
        if auto_commit:
            await session.commit()
        return {"signal": signal, "order": None, "action": action, "reason": reason}

    price = _money(_dec(order_price_override)) if order_price_override is not None else _dec(kline["close"])
    target_position = _dec(state.target_position)
    target_value = _money(total_asset * target_position)
    direction = "买入" if action == "BUY" else "卖出"
    frozen_amount = Decimal("0")
    volume = 0
    no_order_reason: str | None = None

    if direction == "买入":
        delta_value = max(target_value - market_value, Decimal("0"))
        raw_shares = int((delta_value / price).to_integral_value(rounding=ROUND_FLOOR))
        volume = raw_shares // LOT_SIZE * LOT_SIZE
        calculator = AShareCostCalculator(_fee_config(account.get("config"), _global_fee_config(account)))
        available_cash = _dec(account["available_cash"])
        while volume >= LOT_SIZE:
            gross_amount = _money(price * Decimal(volume))
            fees = calculator.calculate(direction, gross_amount)
            frozen_amount = _money(gross_amount + fees.total_fee)
            if frozen_amount <= available_cash:
                break
            volume -= LOT_SIZE
        if volume < LOT_SIZE:
            no_order_reason = "目标买入金额或可用资金不足一手，未下单"
    else:
        if not position or int(position["available_shares"]) <= 0:
            blocked_reason = "无可用持仓"
        else:
            sell_value = max(market_value - target_value, Decimal("0"))
            raw_shares = int((sell_value / price).to_integral_value(rounding=ROUND_FLOOR))
            volume = min(int(position["available_shares"]), raw_shares // LOT_SIZE * LOT_SIZE)
            if action == "SELL_ALL":
                volume = int(position["available_shares"]) // LOT_SIZE * LOT_SIZE
            if volume < LOT_SIZE:
                blocked_reason = "委托数量不足100股"

    if no_order_reason:
        signal = _strategy_signal_response(strategy_signal_id)
        if signal is None:
            signal = await _insert_signal(
                session,
                user_id=user_id,
                account_id=account_id,
                request=replace(request, reason=no_order_reason),
                current_position=current_position,
                action="HOLD",
                snapshot={**snapshot, "no_order_reason": no_order_reason},
            )
            signal = _serialize_row(signal)
        if auto_commit:
            await session.commit()
        return {"signal": signal, "order": None, "action": "HOLD", "reason": no_order_reason}

    if blocked_reason:
        signal = _strategy_signal_response(strategy_signal_id)
        if signal is None:
            signal = await _insert_signal(
                session,
                user_id=user_id,
                account_id=account_id,
                request=request,
                current_position=current_position,
                action="BLOCKED",
                snapshot={**snapshot, "blocked_reason": blocked_reason},
            )
            signal = _serialize_row(signal)
        if auto_commit:
            await session.commit()
        return {"signal": signal, "order": None, "action": "BLOCKED", "reason": blocked_reason}

    if direction == "卖出" and prevent_duplicate_sell_order:
        pending_result = await session.execute(
            text(
                """
                SELECT *
                FROM sim_orders
                WHERE account_id = :account_id
                  AND ts_code = :ts_code
                  AND direction = '卖出'
                  AND status IN ('待成交', '部分成交')
                ORDER BY submit_time DESC, id DESC
                LIMIT 1
                """
            ),
            {"account_id": account_id, "ts_code": request.ts_code},
        )
        pending_order = pending_result.mappings().one_or_none()
        if pending_order is not None:
            if auto_commit:
                await session.commit()
            return {
                "signal": _strategy_signal_response(strategy_signal_id),
                "order": _serialize_row(dict(pending_order)),
                "action": "HOLD",
                "reason": "已有待成交卖出委托",
            }

    order_signal_id = strategy_signal_id
    order_signal = None
    if order_signal_id is None:
        signal_row = await _insert_signal(
            session,
            user_id=user_id,
            account_id=account_id,
            request=request,
            current_position=current_position,
            action=action,
            snapshot=snapshot,
        )
        order_signal_id = int(signal_row["id"])
        order_signal = _serialize_row(signal_row)
    result = await session.execute(
        text(
            """
            INSERT INTO sim_orders (
                account_id, signal_id, ts_code, direction, order_type, price,
                volume, frozen_amount, status
            ) VALUES (
                :account_id, :signal_id, :ts_code, :direction, '限价', :price,
                :volume, :frozen_amount, '待成交'
            )
            RETURNING *
            """
        ),
        {
            "account_id": account_id,
            "signal_id": order_signal_id,
            "ts_code": request.ts_code,
            "direction": direction,
            "price": price,
            "volume": volume,
            "frozen_amount": frozen_amount,
        },
    )
    order = dict(result.mappings().one())

    if direction == "买入":
        result = await session.execute(
            text(
                """
                UPDATE sim_accounts
                SET available_cash = available_cash - :frozen_amount,
                    frozen_cash = frozen_cash + :frozen_amount,
                    updated_at = NOW()
                WHERE id = :account_id AND available_cash >= :frozen_amount
                """
            ),
            {"account_id": account_id, "frozen_amount": frozen_amount},
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="insufficient available cash (concurrent order may have consumed funds)",
            )
        await session.execute(
            text(
                """
                INSERT INTO sim_cash_flow (account_id, flow_type, amount, balance_after, remark)
                VALUES (
                    :account_id, '冻结', -CAST(:amount AS NUMERIC),
                    (SELECT available_cash FROM sim_accounts WHERE id = :account_id),
                    :remark
                )
                """
            ),
            {"account_id": account_id, "amount": frozen_amount, "remark": f"买入委托冻结 {request.ts_code}"},
        )
    else:
        result = await session.execute(
            text(
                """
                UPDATE sim_positions
                SET available_shares = available_shares - :volume,
                    frozen_shares = frozen_shares + :volume,
                    updated_at = NOW()
                WHERE account_id = :account_id AND ts_code = :ts_code
                  AND available_shares >= :volume
                """
            ),
            {"account_id": account_id, "ts_code": request.ts_code, "volume": volume},
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="insufficient available shares (concurrent order may have consumed shares)",
            )

    match_result: dict[str, Any] | None = None
    if auto_match:
        match_result = await match_order(
            session,
            user_id=user_id,
            order_id=int(order["id"]),
            trade_date=request.trade_date,
            match_mode=auto_match_mode,
            auto_commit=auto_commit,
        )
    elif auto_commit:
        await session.commit()
    response = {"signal": order_signal, "order": _serialize_row(order), "action": action, "reason": ""}
    if match_result is not None:
        response["match"] = match_result
    return response


async def match_order(
    session: AsyncSession,
    *,
    user_id: int,
    order_id: int,
    trade_date: date | None = None,
    match_mode: str = "close",
    realtime_price: Decimal | None = None,
    auto_commit: bool = True,
) -> dict[str, Any]:
    if match_mode not in {"close", "open", "limit"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid match_mode")
    result = await session.execute(
        text(
            """
            SELECT o.*, a.user_id, a.config, p.value AS user_trading_fee_config
            FROM sim_orders o
            JOIN sim_accounts a ON a.id = o.account_id
            LEFT JOIN user_preferences p
              ON p.user_id = a.user_id AND p.key = 'trading_fee'
            WHERE o.id = :order_id AND a.user_id = :user_id
            FOR UPDATE OF o, a
            """
        ),
        {"order_id": order_id, "user_id": user_id},
    )
    order = result.mappings().one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    order = dict(order)
    if order["status"] != "待成交":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="order is not pending")

    match_date = trade_date or date.today()
    calendar = await _get_trade_calendar(session, match_date)
    if not calendar or not calendar["is_open"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="非交易日")
    kline = await _get_kline(session, order["ts_code"], match_date)
    match_mode_used = match_mode
    if not kline or kline.get("close") is None:
        price = _resolve_order_price_fallback(order)
        match_mode_used = "order_price_fallback"
    else:
        is_limit_up, is_limit_down = _computed_limit_flags(kline)
        action, reason = apply_cn_rules(
            "BUY" if order["direction"] == "买入" else "SELL_ALL",
            is_suspended=bool(kline.get("is_suspended")),
            is_limit_up=is_limit_up,
            is_limit_down=is_limit_down,
        )
        if action == "BLOCKED":
            # P1-C fix: transition pending order to 'rejected' on BLOCKED
            # so it no longer lingers in '待成交' status with funds/shares
            # frozen. Mirror cancel_order's unfreeze logic so account
            # balances are restored immediately.
            await session.execute(
                text(
                    """
                    UPDATE sim_orders
                    SET status = 'rejected',
                        reject_reason = :reason,
                        update_time = NOW()
                    WHERE id = :order_id
                    """
                ),
                {"order_id": order_id, "reason": reason},
            )
            account_id = int(order["account_id"])
            if order["direction"] == "买入":
                frozen_amount = _dec(order.get("frozen_amount"))
                if frozen_amount > 0:
                    await session.execute(
                        text(
                            """
                            UPDATE sim_accounts
                            SET available_cash = available_cash + :amount,
                                frozen_cash = frozen_cash - :amount,
                                updated_at = NOW()
                            WHERE id = :account_id
                            """
                        ),
                        {"account_id": account_id, "amount": frozen_amount},
                    )
                    await session.execute(
                        text(
                            """
                            INSERT INTO sim_cash_flow (account_id, flow_type, amount, balance_after, remark)
                            VALUES (
                                :account_id, '解冻', CAST(:amount AS NUMERIC),
                                (SELECT available_cash FROM sim_accounts WHERE id = :account_id),
                                :remark
                            )
                            """
                        ),
                        {
                            "account_id": account_id,
                            "amount": frozen_amount,
                            "remark": f"BLOCKED 解冻 {order['ts_code']} (order_id={order_id})",
                        },
                    )
            else:  # 卖出
                await session.execute(
                    text(
                        """
                        UPDATE sim_positions
                        SET available_shares = available_shares + :volume,
                            frozen_shares = frozen_shares - :volume,
                            updated_at = NOW()
                        WHERE account_id = :account_id AND ts_code = :ts_code
                        """
                    ),
                    {"account_id": account_id, "ts_code": order["ts_code"], "volume": int(order["volume"])},
                )
            if auto_commit:
                await session.commit()
            pending_order = dict(order)
            pending_order["status"] = "rejected"
            pending_order["reject_reason"] = reason
            return {
                "order": _serialize_row(pending_order),
                "status": "已拒绝",
                "matched": False,
                "reason": reason,
                "match_mode_used": match_mode,
            }
        price = _resolve_match_price(order, kline, match_mode, realtime_price=realtime_price)

    account_id = int(order["account_id"])
    ts_code = str(order["ts_code"])
    direction = str(order["direction"])
    volume = int(order["volume"]) - int(order["filled_volume"])
    amount = _money(price * Decimal(volume))
    fees = AShareCostCalculator(_fee_config(order.get("config"), _global_fee_config(order))).calculate(direction, amount)

    trade_result = await session.execute(
        text(
            """
            INSERT INTO sim_trades (
                order_id, account_id, ts_code, direction, price, volume, amount,
                stamp_tax, commission, transfer_fee, total_fee
            ) VALUES (
                :order_id, :account_id, :ts_code, :direction, :price, :volume, :amount,
                :stamp_tax, :commission, :transfer_fee, :total_fee
            )
            RETURNING *
            """
        ),
        {
            "order_id": order_id,
            "account_id": account_id,
            "ts_code": ts_code,
            "direction": direction,
            "price": price,
            "volume": volume,
            "amount": amount,
            "stamp_tax": fees.stamp_tax,
            "commission": fees.commission,
            "transfer_fee": fees.transfer_fee,
            "total_fee": fees.total_fee,
        },
    )
    trade = dict(trade_result.mappings().one())

    if direction == "买入":
        actual_cost = _money(amount + fees.total_fee)
        frozen_amount = _dec(order["frozen_amount"])
        refund = max(frozen_amount - actual_cost, Decimal("0"))
        await session.execute(
            text(
                """
                INSERT INTO sim_positions (
                    account_id, ts_code, shares, available_shares, frozen_shares,
                    avg_cost, current_price, market_value, first_buy_date
                ) VALUES (
                    :account_id, :ts_code, :volume, 0, 0,
                    :avg_cost, :price, :amount, :trade_date
                )
                ON CONFLICT (account_id, ts_code) DO UPDATE SET
                    shares = sim_positions.shares + EXCLUDED.shares,
                    avg_cost = CASE
                        WHEN sim_positions.shares + EXCLUDED.shares = 0 THEN 0
                        ELSE ((sim_positions.avg_cost * sim_positions.shares) + (:actual_cost))
                             / (sim_positions.shares + EXCLUDED.shares)
                    END,
                    current_price = EXCLUDED.current_price,
                    market_value = (sim_positions.shares + EXCLUDED.shares) * EXCLUDED.current_price,
                    first_buy_date = CASE
                        WHEN sim_positions.shares <= 0 THEN EXCLUDED.first_buy_date
                        ELSE COALESCE(sim_positions.first_buy_date, EXCLUDED.first_buy_date)
                    END,
                    unrealized_pnl = 0,
                    profit_rate = 0,
                    updated_at = NOW()
                """
            ),
            {
                "account_id": account_id,
                "ts_code": ts_code,
                "volume": volume,
                "avg_cost": _money(actual_cost / Decimal(volume)),
                "price": price,
                "amount": amount,
                "trade_date": match_date,
                "actual_cost": actual_cost,
            },
        )
        await session.execute(
            text(
                """
                UPDATE sim_accounts
                SET frozen_cash = frozen_cash - :frozen_amount,
                    available_cash = available_cash + :refund,
                    updated_at = NOW()
                WHERE id = :account_id
                """
            ),
            {"account_id": account_id, "frozen_amount": frozen_amount, "refund": refund},
        )
        cash_amount = -amount
    else:
        net_income = _money(amount - fees.total_fee)
        await session.execute(
            text(
                """
                UPDATE sim_positions
                SET shares = shares - CAST(:volume AS INTEGER),
                    frozen_shares = frozen_shares - CAST(:volume AS INTEGER),
                    current_price = CAST(:price AS NUMERIC),
                    market_value = (shares - CAST(:volume AS INTEGER)) * CAST(:price AS NUMERIC),
                    unrealized_pnl = CASE
                        WHEN shares - CAST(:volume AS INTEGER) <= 0
                            THEN 0
                        ELSE (CAST(:price AS NUMERIC) - avg_cost) * (shares - CAST(:volume AS INTEGER))
                    END,
                    profit_rate = CASE
                        WHEN shares - CAST(:volume AS INTEGER) <= 0
                            THEN 0
                        WHEN avg_cost <= 0
                            THEN 0
                        ELSE (CAST(:price AS NUMERIC) - avg_cost) / avg_cost
                    END,
                    available_shares = LEAST(available_shares, shares - CAST(:volume AS INTEGER)),
                    updated_at = NOW()
                WHERE account_id = :account_id AND ts_code = :ts_code
                """
            ),
            {
                "account_id": account_id,
                "ts_code": ts_code,
                "volume": volume,
                "price": price,
                "amount": amount,
                "total_fee": fees.total_fee,
            },
        )
        await session.execute(
            text(
                """
                UPDATE sim_accounts
                SET available_cash = available_cash + :net_income,
                    updated_at = NOW()
                WHERE id = :account_id
                """
            ),
            {"account_id": account_id, "net_income": net_income},
        )
        cash_amount = amount

    await session.execute(
        text(
            """
            UPDATE sim_orders
            SET filled_volume = volume,
                status = '全部成交',
                update_time = NOW()
            WHERE id = :order_id
            """
        ),
        {"order_id": order_id},
    )
    await session.execute(
        text(
            """
            INSERT INTO sim_cash_flow (account_id, related_trade_id, flow_type, amount, balance_after, remark)
            VALUES (
                :account_id, :trade_id, :flow_type, :amount,
                (SELECT available_cash FROM sim_accounts WHERE id = :account_id),
                :remark
            )
            """
        ),
        {
            "account_id": account_id,
            "trade_id": trade["id"],
            "flow_type": direction,
            "amount": cash_amount,
            "remark": f"{direction}成交 {ts_code}",
        },
    )
    if fees.total_fee > 0:
        await session.execute(
            text(
                """
                INSERT INTO sim_cash_flow (account_id, related_trade_id, flow_type, amount, balance_after, remark)
                VALUES (
                    :account_id, :trade_id, '手续费', -CAST(:amount AS NUMERIC),
                    (SELECT available_cash FROM sim_accounts WHERE id = :account_id),
                    :remark
                )
                """
            ),
            {"account_id": account_id, "trade_id": trade["id"], "amount": fees.total_fee, "remark": "成交费用"},
        )

    await refresh_account_assets(session, account_id=account_id)
    if auto_commit:
        await session.commit()
    return {"trade": _serialize_row(trade), "status": "全部成交", "matched": True, "match_mode_used": match_mode_used}


async def cancel_order(session: AsyncSession, *, user_id: int, order_id: int) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT o.*, a.user_id
            FROM sim_orders o
            JOIN sim_accounts a ON a.id = o.account_id
            WHERE o.id = :order_id AND a.user_id = :user_id
            FOR UPDATE
            """
        ),
        {"order_id": order_id, "user_id": user_id},
    )
    order = result.mappings().one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    order = dict(order)
    if order["status"] != "待成交":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="only pending orders can be cancelled")

    account_id = int(order["account_id"])
    if order["direction"] == "买入":
        frozen_amount = _dec(order["frozen_amount"])
        await session.execute(
            text(
                """
                UPDATE sim_accounts
                SET available_cash = available_cash + :amount,
                    frozen_cash = frozen_cash - :amount,
                    updated_at = NOW()
                WHERE id = :account_id
                """
            ),
            {"account_id": account_id, "amount": frozen_amount},
        )
    else:
        await session.execute(
            text(
                """
                UPDATE sim_positions
                SET available_shares = available_shares + :volume,
                    frozen_shares = frozen_shares - :volume,
                    updated_at = NOW()
                WHERE account_id = :account_id AND ts_code = :ts_code
                """
            ),
            {"account_id": account_id, "ts_code": order["ts_code"], "volume": int(order["volume"])},
        )

    await session.execute(
        text("UPDATE sim_orders SET status = '已撤单', cancel_time = NOW(), update_time = NOW() WHERE id = :order_id"),
        {"order_id": order_id},
    )
    await session.commit()
    return {"status": "已撤单", "order_id": order_id}


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

    stopped = await check_stop_conditions(session, account_id=account_id, nav_date=nav_date)
    for s in stopped:
        order_signal = SignalOrderRequest(
            ts_code=s["ts_code"],
            signal_type="卖出",
            trade_date=nav_date,
        )
        await generate_order_from_signal(
            session,
            user_id=0,
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
