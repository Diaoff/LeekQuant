"""Simulation trading service for A-share signal execution."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.cost import AShareCostCalculator, FeeConfig
from app.backtest.signals import SignalInput, apply_cn_rules, map_signal_to_action

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
    return value.quantize(MONEY_QUANT)


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _fee_config(config: dict[str, Any] | None) -> FeeConfig:
    fee_cfg = (config or {}).get("fee_config") if isinstance(config, dict) else None
    if not isinstance(fee_cfg, dict):
        return FeeConfig()
    return FeeConfig(
        commission_rate=_dec(fee_cfg.get("commission_rate"), "0.00025"),
        min_commission=_dec(fee_cfg.get("min_commission"), "5.0"),
        stamp_tax_rate=_dec(fee_cfg.get("stamp_tax_rate"), "0.0005"),
        transfer_fee_rate=_dec(fee_cfg.get("transfer_fee_rate"), "0.00001"),
    )


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


async def get_account_or_404(session: AsyncSession, account_id: int, user_id: int) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT id, user_id, strategy_id, name, initial_cash, available_cash,
                   frozen_cash, total_asset, status, config, created_at, updated_at
            FROM sim_accounts
            WHERE id = :id AND user_id = :user_id
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
            ORDER BY a.updated_at DESC, a.id DESC
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


async def list_child_rows(
    session: AsyncSession,
    *,
    user_id: int,
    account_id: int,
    table: str,
    order_by: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    await get_account_or_404(session, account_id, user_id)
    limit = min(max(limit, 1), 500)
    result = await session.execute(
        text(
            f"""
            SELECT *
            FROM {table}
            WHERE account_id = :account_id
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


async def _get_position(session: AsyncSession, account_id: int, ts_code: str) -> dict[str, Any] | None:
    result = await session.execute(
        text("SELECT * FROM sim_positions WHERE account_id = :account_id AND ts_code = :ts_code"),
        {"account_id": account_id, "ts_code": ts_code},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


def _resolve_match_price(order: dict[str, Any], kline: dict[str, Any], match_mode: str) -> Decimal:
    if match_mode == "open":
        if kline.get("open") is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily open price not found")
        return _dec(kline["open"])
    if match_mode == "close":
        if kline.get("close") is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily close price not found")
        return _dec(kline["close"])

    order_price = _dec(order.get("price"))
    low = _dec(kline.get("low")) if kline.get("low") is not None else None
    high = _dec(kline.get("high")) if kline.get("high") is not None else None
    if low is None or high is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily high/low not found")
    if order_price < low or order_price > high:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="限价未触达")
    return order_price


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


async def generate_order_from_signal(
    session: AsyncSession,
    *,
    user_id: int,
    account_id: int,
    request: SignalOrderRequest,
) -> dict[str, Any]:
    account = await get_account_or_404(session, account_id, user_id)
    if account["status"] != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="account is not active")

    calendar = await _get_trade_calendar(session, request.trade_date)
    position = await _get_position(session, account_id, request.ts_code)
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
        signal = await _insert_signal(
            session,
            user_id=user_id,
            account_id=account_id,
            request=request,
            current_position=current_position,
            action="BLOCKED",
            snapshot={**snapshot, "blocked_reason": "非交易日"},
        )
        await session.commit()
        return {"signal": _serialize_row(signal), "order": None, "action": "BLOCKED", "reason": "非交易日"}

    kline = await _get_kline(session, request.ts_code, request.trade_date)
    if not kline or kline.get("close") is None:
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
        signal = await _insert_signal(
            session,
            user_id=user_id,
            account_id=account_id,
            request=request,
            current_position=current_position,
            action=action,
            snapshot={**snapshot, "blocked_reason": blocked_reason} if blocked_reason else snapshot,
        )
        await session.commit()
        return {"signal": _serialize_row(signal), "order": None, "action": action, "reason": reason}

    price = _dec(kline["close"])
    target_position = _dec(state.target_position)
    target_value = _money(total_asset * target_position)
    direction = "买入" if action == "BUY" else "卖出"
    frozen_amount = Decimal("0")
    volume = 0

    if direction == "买入":
        delta_value = max(target_value - market_value, Decimal("0"))
        raw_shares = int((delta_value / price).to_integral_value(rounding=ROUND_FLOOR))
        volume = raw_shares // LOT_SIZE * LOT_SIZE
        calculator = AShareCostCalculator(_fee_config(account.get("config")))
        available_cash = _dec(account["available_cash"])
        while volume >= LOT_SIZE:
            gross_amount = _money(price * Decimal(volume))
            fees = calculator.calculate(direction, gross_amount)
            frozen_amount = _money(gross_amount + fees.total_fee)
            if frozen_amount <= available_cash:
                break
            volume -= LOT_SIZE
        if volume < LOT_SIZE:
            blocked_reason = "委托数量不足100股"
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

    if blocked_reason:
        signal = await _insert_signal(
            session,
            user_id=user_id,
            account_id=account_id,
            request=request,
            current_position=current_position,
            action="BLOCKED",
            snapshot={**snapshot, "blocked_reason": blocked_reason},
        )
        await session.commit()
        return {"signal": _serialize_row(signal), "order": None, "action": "BLOCKED", "reason": blocked_reason}

    signal = await _insert_signal(
        session,
        user_id=user_id,
        account_id=account_id,
        request=request,
        current_position=current_position,
        action=action,
        snapshot=snapshot,
    )
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
            "signal_id": signal["id"],
            "ts_code": request.ts_code,
            "direction": direction,
            "price": price,
            "volume": volume,
            "frozen_amount": frozen_amount,
        },
    )
    order = dict(result.mappings().one())

    if direction == "买入":
        await session.execute(
            text(
                """
                UPDATE sim_accounts
                SET available_cash = available_cash - :frozen_amount,
                    frozen_cash = frozen_cash + :frozen_amount,
                    updated_at = NOW()
                WHERE id = :account_id
                """
            ),
            {"account_id": account_id, "frozen_amount": frozen_amount},
        )
        await session.execute(
            text(
                """
                INSERT INTO sim_cash_flow (account_id, flow_type, amount, balance_after, remark)
                VALUES (
                    :account_id, '冻结', -:amount,
                    (SELECT available_cash FROM sim_accounts WHERE id = :account_id),
                    :remark
                )
                """
            ),
            {"account_id": account_id, "amount": frozen_amount, "remark": f"买入委托冻结 {request.ts_code}"},
        )
    else:
        await session.execute(
            text(
                """
                UPDATE sim_positions
                SET available_shares = available_shares - :volume,
                    frozen_shares = frozen_shares + :volume,
                    updated_at = NOW()
                WHERE account_id = :account_id AND ts_code = :ts_code
                """
            ),
            {"account_id": account_id, "ts_code": request.ts_code, "volume": volume},
        )

    await session.commit()
    return {"signal": _serialize_row(signal), "order": _serialize_row(order), "action": action, "reason": ""}


async def match_order(
    session: AsyncSession,
    *,
    user_id: int,
    order_id: int,
    trade_date: date | None = None,
    match_mode: str = "close",
) -> dict[str, Any]:
    if match_mode not in {"close", "open", "limit"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid match_mode")
    result = await session.execute(
        text(
            """
            SELECT o.*, a.user_id, a.config
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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="order is not pending")

    match_date = trade_date or date.today()
    calendar = await _get_trade_calendar(session, match_date)
    if not calendar or not calendar["is_open"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="非交易日")
    kline = await _get_kline(session, order["ts_code"], match_date)
    if not kline or kline.get("close") is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily kline not found")
    is_limit_up, is_limit_down = _computed_limit_flags(kline)
    action, reason = apply_cn_rules(
        "BUY" if order["direction"] == "买入" else "SELL_ALL",
        is_suspended=bool(kline.get("is_suspended")),
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
    )
    if action == "BLOCKED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)

    account_id = int(order["account_id"])
    ts_code = str(order["ts_code"])
    direction = str(order["direction"])
    volume = int(order["volume"]) - int(order["filled_volume"])
    price = _resolve_match_price(order, kline, match_mode)
    amount = _money(price * Decimal(volume))
    fees = AShareCostCalculator(_fee_config(order.get("config"))).calculate(direction, amount)

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
                    first_buy_date = COALESCE(sim_positions.first_buy_date, EXCLUDED.first_buy_date),
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
                SET shares = shares - :volume,
                    frozen_shares = frozen_shares - :volume,
                    current_price = :price,
                    market_value = (shares - :volume) * :price,
                    available_shares = LEAST(available_shares, shares - :volume),
                    updated_at = NOW()
                WHERE account_id = :account_id AND ts_code = :ts_code
                """
            ),
            {"account_id": account_id, "ts_code": ts_code, "volume": volume, "price": price},
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
                    :account_id, :trade_id, '手续费', -:amount,
                    (SELECT available_cash FROM sim_accounts WHERE id = :account_id),
                    :remark
                )
                """
            ),
            {"account_id": account_id, "trade_id": trade["id"], "amount": fees.total_fee, "remark": "成交费用"},
        )

    await refresh_account_assets(session, account_id=account_id)
    await session.commit()
    return {"trade": _serialize_row(trade), "status": "全部成交"}


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
                    SELECT SUM(market_value) FROM sim_positions WHERE account_id = :account_id
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
              AND dk.ts_code = p.ts_code
              AND dk.trade_date = :nav_date
              AND dk.close IS NOT NULL
            """
        ),
        {"account_id": account_id, "nav_date": nav_date},
    )
    return int(result.rowcount or 0)


async def unlock_t1_positions(session: AsyncSession, *, trade_date: date) -> int:
    calendar = await _get_trade_calendar(session, trade_date)
    if not calendar or not calendar["is_open"] or not calendar.get("pretrade_date"):
        return 0
    prev_date = calendar["pretrade_date"]
    result = await session.execute(
        text(
            """
            WITH buy_trades AS (
                SELECT account_id, ts_code, SUM(volume)::INTEGER AS volume
                FROM sim_trades
                WHERE direction = '买入' AND trade_time::DATE = :prev_date
                GROUP BY account_id, ts_code
            )
            UPDATE sim_positions p
            SET available_shares = LEAST(p.shares, p.available_shares + b.volume),
                updated_at = NOW()
            FROM buy_trades b
            WHERE p.account_id = b.account_id AND p.ts_code = b.ts_code
            """
        ),
        {"prev_date": prev_date},
    )
    await session.commit()
    return int(result.rowcount or 0)


async def snapshot_daily_nav(session: AsyncSession, *, account_id: int, nav_date: date) -> dict[str, Any]:
    await refresh_position_market_values(session, account_id=account_id, nav_date=nav_date)
    await refresh_account_assets(session, account_id=account_id)
    account_result = await session.execute(
        text("SELECT * FROM sim_accounts WHERE id = :account_id"),
        {"account_id": account_id},
    )
    account = dict(account_result.mappings().one())
    pos_result = await session.execute(
        text("SELECT COALESCE(SUM(market_value), 0) FROM sim_positions WHERE account_id = :account_id"),
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
