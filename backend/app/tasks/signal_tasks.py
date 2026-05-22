"""Celery tasks for daily strategy signal generation."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.backtest.adapter import BacktestContext, KBar
from app.libs import MyTT
from app.sim.service import SignalOrderRequest, _insert_signal, generate_order_from_signal
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked

LOOKBACK_BARS = 60


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _parse_kbar(row: dict[str, Any]) -> KBar:
    return KBar(
        ts_code=row["ts_code"],
        trade_date=row["trade_date"] if isinstance(row["trade_date"], date) else date.fromisoformat(str(row["trade_date"])),
        open=_dec(row.get("open")),
        high=_dec(row.get("high")),
        low=_dec(row.get("low")),
        close=_dec(row.get("close")),
        pre_close=_dec(row.get("pre_close")),
        volume=int(row.get("volume") or 0),
        amount=_dec(row.get("amount")),
        adj_factor=_dec(row.get("adj_factor")) if row.get("adj_factor") is not None else None,
        is_suspended=bool(row.get("is_suspended", False)),
        is_limit_up=bool(row.get("is_limit_up", False)),
        is_limit_down=bool(row.get("is_limit_down", False)),
    )


def _exec_strategy(source_code: str, ctx: BacktestContext) -> dict[str, Any] | None:
    sandbox: dict[str, Any] = {"ctx": ctx}
    for name in dir(MyTT):
        if not name.startswith("_"):
            sandbox[name] = getattr(MyTT, name)
    exec(source_code, sandbox)
    func = sandbox.get("generate_signal")
    if func is None:
        return None
    result = func(ctx)
    return result if isinstance(result, dict) else None


async def _last_open_trade_date(session, requested: date | None) -> date | None:
    if requested is not None:
        return requested
    result = await session.execute(
        text(
            """
            SELECT cal_date
            FROM trade_calendar
            WHERE is_open = TRUE AND cal_date <= CURRENT_DATE
            ORDER BY cal_date DESC
            LIMIT 1
            """
        )
    )
    return result.scalar_one_or_none()


async def _active_strategies(session) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, user_id, name, source_code
            FROM strategies
            WHERE status = 'active'
            ORDER BY id
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def _stock_codes(session) -> list[str]:
    result = await session.execute(
        text(
            """
            SELECT ts_code
            FROM stock_basic
            WHERE is_delisted = FALSE
            ORDER BY symbol
            """
        )
    )
    return [row["ts_code"] for row in result.mappings().all()]


async def _bound_accounts(session, strategy_id: int) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, user_id
            FROM sim_accounts
            WHERE status = 'active' AND strategy_id = :strategy_id
            ORDER BY id
            """
        ),
        {"strategy_id": strategy_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def _recent_klines(session, ts_code: str, trade_date: date) -> list[KBar]:
    result = await session.execute(
        text(
            """
            SELECT ts_code, trade_date, open, high, low, close, pre_close,
                   volume, amount, adj_factor, is_suspended,
                   is_limit_up, is_limit_down
            FROM daily_kline
            WHERE ts_code = :ts_code AND trade_date <= :trade_date
            ORDER BY trade_date DESC
            LIMIT :limit
            """
        ),
        {"ts_code": ts_code, "trade_date": trade_date, "limit": LOOKBACK_BARS},
    )
    rows = [dict(row) for row in result.mappings().all()]
    return [_parse_kbar(row) for row in reversed(rows)]


async def generate_all_signals_for_date(session, *, trade_date: date | None = None) -> dict[str, Any]:
    run_date = await _last_open_trade_date(session, trade_date)
    if run_date is None:
        return {"skipped": True, "reason": "no open trade date"}

    strategies = await _active_strategies(session)
    stock_codes = await _stock_codes(session)
    stats: dict[str, Any] = {
        "trade_date": run_date.isoformat(),
        "strategy_count": len(strategies),
        "stock_count": len(stock_codes),
        "signals_logged": 0,
        "orders_created": 0,
        "errors": [],
    }
    accounts_by_strategy: dict[int, list[dict[str, Any]]] = {}

    for strategy in strategies:
        strategy_id = int(strategy["id"])
        accounts_by_strategy[strategy_id] = await _bound_accounts(session, strategy_id)
        for ts_code in stock_codes:
            try:
                klines = await _recent_klines(session, ts_code, run_date)
                if not klines or klines[-1].trade_date != run_date:
                    continue
                ctx = BacktestContext(klines, {}, Decimal("0"))
                signal = _exec_strategy(strategy["source_code"], ctx)
                if not signal:
                    continue

                request = SignalOrderRequest(
                    ts_code=ts_code,
                    signal_type=str(signal.get("signal_type") or "观望"),
                    trade_date=run_date,
                    strategy_id=strategy_id,
                    target_position=_dec(signal.get("target_position")) if signal.get("target_position") is not None else None,
                    confidence=_dec(signal.get("confidence")) if signal.get("confidence") is not None else None,
                    reason=signal.get("reason"),
                    snapshot={
                        "strategy_name": strategy.get("name"),
                        "close": str(klines[-1].close),
                        "source": "generate_all_signals",
                    },
                )
                await _insert_signal(
                    session,
                    user_id=int(strategy["user_id"]),
                    account_id=None,
                    request=request,
                    current_position=Decimal(str(signal.get("current_position", 0))),
                    action="PENDING",
                    snapshot=request.snapshot or {},
                )
                stats["signals_logged"] += 1

                for account in accounts_by_strategy[strategy_id]:
                    order_result = await generate_order_from_signal(
                        session,
                        user_id=int(account["user_id"]),
                        account_id=int(account["id"]),
                        request=request,
                    )
                    if order_result.get("order") is not None:
                        stats["orders_created"] += 1
            except Exception as exc:
                stats["errors"].append({"strategy_id": strategy_id, "ts_code": ts_code, "error": str(exc)})
                await session.rollback()

    await session.commit()
    stats["error_count"] = len(stats["errors"])
    return stats


@celery_app.task(name="app.tasks.signal_tasks.generate_all_signals", bind=True)
def generate_all_signals(self, trade_date: str | None = None) -> dict[str, Any]:
    run_date = date.fromisoformat(trade_date) if trade_date else None
    return asyncio.run(
        _run_tracked(
            "generate_all_signals",
            self.request.id,
            {"trade_date": run_date},
            lambda session: generate_all_signals_for_date(session, trade_date=run_date),
        )
    )
