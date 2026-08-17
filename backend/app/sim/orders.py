"""Signal -> order generation, order matching, cancel."""

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

from app.sim._helpers import (
    _fee_config, _global_fee_config, _get_kline,
    _get_latest_kline_before_or_on, _get_position, _get_trade_calendar,
)
from app.sim.accounts import get_account_or_404
from app.sim.nav import refresh_account_assets

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
