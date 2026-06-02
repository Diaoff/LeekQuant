from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import async_session_factory
from app.realtime.bus import RealtimeBus, RealtimeSubscription, get_realtime_bus
from app.realtime.models import RealtimeTick
from app.realtime.providers import EastMoneyRealtimeProvider
from app.sim.service import SignalOrderRequest, generate_order_from_signal, match_order, unlock_t1_positions

logger = logging.getLogger(__name__)

TASK_NAME = "realtime_risk_guard"
RUN_ID = "realtime_risk_guard:heartbeat"


@dataclass(frozen=True, slots=True)
class GuardPosition:
    account_id: int
    user_id: int
    strategy_id: int | None
    ts_code: str
    avg_cost: Decimal
    shares: int
    available_shares: int
    stop_loss_pct: Decimal
    take_profit_pct: Decimal


def _risk_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    risk_cfg = config.get("risk_config")
    return risk_cfg if isinstance(risk_cfg, dict) else {}


def _risk_decimal(value: Any) -> Decimal:
    if value in (None, "", 0):
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, default=str)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _flatten_positions(positions_by_code: dict[str, list[GuardPosition]]) -> list[GuardPosition]:
    return [position for positions in positions_by_code.values() for position in positions]


def _preflight_blocked_reason(position: GuardPosition, tick: RealtimeTick) -> str | None:
    if position.avg_cost <= 0:
        return None
    profit_rate = (tick.price - position.avg_cost) / position.avg_cost
    stop_triggered = position.stop_loss_pct > 0 and profit_rate <= -position.stop_loss_pct
    profit_triggered = position.take_profit_pct > 0 and profit_rate >= position.take_profit_pct
    if not (stop_triggered or profit_triggered):
        return None
    if position.available_shares <= 0 and position.shares >= 100:
        return "T+1 未解锁或无可卖持仓"
    if position.available_shares < 100:
        return "可卖不足一手"
    return None


def _aggressive_sell_price(tick: RealtimeTick) -> Decimal:
    base_price = tick.bid1 or tick.price
    return max(base_price - Decimal("0.01"), Decimal("0.01")).quantize(Decimal("0.0001"))


async def write_risk_guard_heartbeat(
    session: AsyncSession,
    *,
    refresh_interval_seconds: float,
    loaded_positions: int,
    tracked_symbols: int,
    trade_date: date | None = None,
    last_error: str | None = None,
    last_trigger: dict[str, Any] | None = None,
    last_blocked_reason: str | None = None,
    missing_ticks: list[str] | None = None,
) -> None:
    now = _utc_now()
    payload = {
        "last_seen_at": now.isoformat(),
        "trade_date": trade_date.isoformat() if trade_date else None,
        "refresh_interval_seconds": refresh_interval_seconds,
        "loaded_positions": loaded_positions,
        "tracked_symbols": tracked_symbols,
        "last_error": last_error,
        "last_trigger": last_trigger,
        "last_blocked_reason": last_blocked_reason,
        "missing_ticks": missing_ticks or [],
    }
    update_result = await session.execute(
        text(
            """
            UPDATE task_runs
            SET task_name = :task_name,
                status = 'running',
                started_at = :started_at,
                finished_at = NULL,
                duration_ms = NULL,
                payload = CAST(:payload AS JSONB),
                result = CAST(:result AS JSONB),
                error_message = :error_message
            WHERE task_name = :task_name AND task_id = :task_id
            """
        ),
        {
            "task_name": TASK_NAME,
            "task_id": RUN_ID,
            "started_at": now,
            "payload": _json_dumps(
                {
                    "mode": "snapshot",
                    "refresh_interval_seconds": refresh_interval_seconds,
                    "trade_date": trade_date,
                }
            ),
            "result": _json_dumps(payload),
            "error_message": last_error,
        },
    )
    if int(update_result.rowcount or 0) == 0:
        await session.execute(
            text(
                """
                INSERT INTO task_runs (
                    task_name, task_id, status, started_at, payload, result, error_message
                ) VALUES (
                    :task_name, :task_id, 'running', :started_at,
                    CAST(:payload AS JSONB), CAST(:result AS JSONB), :error_message
                )
                """
            ),
            {
                "task_name": TASK_NAME,
                "task_id": RUN_ID,
                "started_at": now,
                "payload": _json_dumps(
                    {
                        "mode": "snapshot",
                        "refresh_interval_seconds": refresh_interval_seconds,
                        "trade_date": trade_date,
                    }
                ),
                "result": _json_dumps(payload),
                "error_message": last_error,
            },
        )
    await session.commit()


def _parse_seen_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        seen = value
    elif isinstance(value, str) and value:
        seen = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return None
    if seen.tzinfo is None:
        return seen.replace(tzinfo=timezone.utc)
    return seen.astimezone(timezone.utc)


async def get_risk_guard_status(
    session: AsyncSession,
    *,
    default_interval_seconds: float = 30.0,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT started_at, payload, result, error_message
            FROM task_runs
            WHERE task_name = :task_name
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"task_name": TASK_NAME},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return {
            "status": "missing",
            "last_seen_at": None,
            "seconds_since_seen": None,
            "loaded_positions": 0,
            "tracked_symbols": 0,
            "last_error": None,
            "last_trigger": None,
            "last_blocked_reason": None,
        }

    result_payload = row["result"]
    payload = result_payload if isinstance(result_payload, dict) else {}
    payload_cfg = row["payload"]
    cfg = payload_cfg if isinstance(payload_cfg, dict) else {}
    seen_at = _parse_seen_at(payload.get("last_seen_at")) or _parse_seen_at(row["started_at"])
    seconds_since_seen: int | None = None
    status_value = "missing"
    if seen_at is not None:
        seconds_since_seen = int((_utc_now() - seen_at).total_seconds())
        interval = float(payload.get("refresh_interval_seconds") or cfg.get("refresh_interval_seconds") or default_interval_seconds)
        stale_after = max(60.0, 4.0 * interval)
        status_value = "stale" if seconds_since_seen > stale_after else "running"

    return {
        "status": status_value,
        "last_seen_at": seen_at.isoformat() if seen_at else None,
        "seconds_since_seen": seconds_since_seen,
        "loaded_positions": int(payload.get("loaded_positions") or 0),
        "tracked_symbols": int(payload.get("tracked_symbols") or 0),
        "last_error": row["error_message"] or payload.get("last_error"),
        "last_trigger": payload.get("last_trigger"),
        "last_blocked_reason": payload.get("last_blocked_reason"),
    }


async def load_guard_positions(session: AsyncSession) -> dict[str, list[GuardPosition]]:
    result = await session.execute(
        text(
            """
            SELECT a.id AS account_id, a.user_id, a.strategy_id, a.config,
                   p.ts_code, p.avg_cost, p.shares, p.available_shares
            FROM sim_positions p
            JOIN sim_accounts a ON a.id = p.account_id
            WHERE a.status = 'active'
              AND p.shares > 0
              AND p.avg_cost > 0
            ORDER BY p.ts_code, a.id
            """
        )
    )
    grouped: dict[str, list[GuardPosition]] = defaultdict(list)
    for row in result.mappings().all():
        risk_cfg = _risk_config(row["config"])
        stop_loss = _risk_decimal(risk_cfg.get("stop_loss_pct"))
        take_profit = _risk_decimal(risk_cfg.get("take_profit_pct"))
        if stop_loss <= 0 and take_profit <= 0:
            continue
        position = GuardPosition(
            account_id=int(row["account_id"]),
            user_id=int(row["user_id"]),
            strategy_id=int(row["strategy_id"]) if row["strategy_id"] is not None else None,
            ts_code=str(row["ts_code"]),
            avg_cost=_risk_decimal(row["avg_cost"]),
            shares=int(row["shares"]),
            available_shares=int(row["available_shares"]),
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
        )
        grouped[position.ts_code].append(position)
    return dict(grouped)


async def refresh_tick_position_value(session: AsyncSession, *, position: GuardPosition, tick: RealtimeTick) -> None:
    await session.execute(
        text(
            """
            UPDATE sim_positions
            SET current_price = CAST(:price AS NUMERIC),
                market_value = shares * CAST(:price AS NUMERIC),
                unrealized_pnl = (CAST(:price AS NUMERIC) - avg_cost) * shares,
                profit_rate = CASE
                    WHEN avg_cost > 0 THEN (CAST(:price AS NUMERIC) - avg_cost) / avg_cost
                    ELSE 0
                END,
                updated_at = NOW()
            WHERE account_id = :account_id AND ts_code = :ts_code
            """
        ),
        {"account_id": position.account_id, "ts_code": position.ts_code, "price": tick.price},
    )


async def trigger_realtime_stop_order(
    session: AsyncSession,
    *,
    position: GuardPosition,
    tick: RealtimeTick,
    trade_date: date,
) -> dict[str, Any] | None:
    if position.available_shares < 100:
        logger.info(
            "realtime risk guard skipped: insufficient sellable shares",
            extra={
                "account_id": position.account_id,
                "ts_code": position.ts_code,
                "available_shares": position.available_shares,
            },
        )
        return None

    profit_rate = (tick.price - position.avg_cost) / position.avg_cost
    exit_reason: str | None = None
    if position.stop_loss_pct > 0 and profit_rate <= -position.stop_loss_pct:
        exit_reason = "止损"
    elif position.take_profit_pct > 0 and profit_rate >= position.take_profit_pct:
        exit_reason = "止盈"
    if exit_reason is None:
        return None

    logger.info(
        "realtime risk guard triggered",
        extra={
            "account_id": position.account_id,
            "user_id": position.user_id,
            "strategy_id": position.strategy_id,
            "ts_code": position.ts_code,
            "avg_cost": str(position.avg_cost),
            "trigger_price": str(tick.price),
            "profit_rate": str(profit_rate),
            "stop_loss_pct": str(position.stop_loss_pct),
            "take_profit_pct": str(position.take_profit_pct),
            "exit_reason": exit_reason,
        },
    )
    await refresh_tick_position_value(session, position=position, tick=tick)
    order_price = _aggressive_sell_price(tick)
    result = await generate_order_from_signal(
        session,
        user_id=position.user_id,
        account_id=position.account_id,
        request=SignalOrderRequest(
            ts_code=position.ts_code,
            signal_type="卖出",
            trade_date=trade_date,
            strategy_id=position.strategy_id,
            reason=exit_reason,
            snapshot={
                "source": "realtime_risk_guard",
                "trigger_price": str(tick.price),
                "order_price": str(order_price),
                "profit_rate": str(profit_rate),
                "exit_reason": exit_reason,
                "tick_ts": tick.ts.isoformat(),
            },
        ),
        exit_reason_override=exit_reason,
        order_price_override=order_price,
        prevent_duplicate_sell_order=True,
        allow_missing_kline_with_order_price=True,
    )
    logger.info(
        "realtime risk guard order result",
        extra={
            "account_id": position.account_id,
            "ts_code": position.ts_code,
            "exit_reason": exit_reason,
            "action": result.get("action"),
            "reason": result.get("reason"),
            "has_order": result.get("order") is not None,
        },
    )
    order = result.get("order")
    if order and order.get("direction") == "卖出":
        match_result = await match_order(
            session,
            user_id=position.user_id,
            order_id=int(order["id"]),
            trade_date=trade_date,
            match_mode="limit",
        )
        result["match"] = match_result
        logger.info(
            "realtime risk guard matched sell order",
            extra={
                "account_id": position.account_id,
                "ts_code": position.ts_code,
                "order_id": order["id"],
                "match_mode_used": match_result.get("match_mode_used"),
            },
        )
    return result


class RealtimeRiskGuard:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        bus: RealtimeBus | None = None,
        refresh_interval_seconds: float = 30.0,
    ) -> None:
        self.session_factory = session_factory
        self.bus = bus or get_realtime_bus()
        self.refresh_interval_seconds = refresh_interval_seconds
        self.positions_by_code: dict[str, list[GuardPosition]] = {}

    async def refresh_positions(self, subscription: RealtimeSubscription, *, trade_date: date) -> None:
        async with self.session_factory() as session:
            await unlock_t1_positions(session, trade_date=trade_date)
            positions_by_code = await load_guard_positions(session)
        old_codes = set(self.positions_by_code)
        new_codes = set(positions_by_code)
        await subscription.unsubscribe(old_codes - new_codes)
        await subscription.subscribe(new_codes - old_codes)
        self.positions_by_code = positions_by_code

    async def handle_tick(self, tick: RealtimeTick, *, trade_date: date) -> list[dict[str, Any]]:
        positions = self.positions_by_code.get(tick.ts_code, [])
        triggered: list[dict[str, Any]] = []
        for position in positions:
            try:
                async with self.session_factory() as session:
                    result = await trigger_realtime_stop_order(
                        session,
                        position=position,
                        tick=tick,
                        trade_date=trade_date,
                    )
                    if result is not None:
                        triggered.append(result)
            except Exception as exc:
                reason = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
                triggered.append(
                    {
                        "action": "BLOCKED",
                        "reason": reason,
                        "order": None,
                        "ts_code": position.ts_code,
                        "account_id": position.account_id,
                    }
                )
                logger.exception(
                    "realtime risk guard failed to process position",
                    extra={
                        "account_id": position.account_id,
                        "ts_code": position.ts_code,
                        "trade_date": trade_date.isoformat(),
                        "tick_price": str(tick.price),
                    },
                )
        return triggered

    async def _refresh_positions_for_subscription(self, subscription: RealtimeSubscription, *, trade_date: date) -> None:
        try:
            await self.refresh_positions(subscription, trade_date=trade_date)
        except Exception:
            logger.exception("realtime risk guard failed to refresh subscriptions")

    async def run(self, *, trade_date: date | None = None) -> None:
        run_date = trade_date or date.today()
        subscription = await self.bus.open_subscription()
        refresh_task: asyncio.Task[None] | None = None
        try:
            await self._refresh_positions_for_subscription(subscription, trade_date=run_date)

            async def refresh_loop() -> None:
                while True:
                    await asyncio.sleep(self.refresh_interval_seconds)
                    await self._refresh_positions_for_subscription(subscription, trade_date=run_date)

            refresh_task = asyncio.create_task(refresh_loop())
            async for tick in subscription.listen():
                await self.handle_tick(tick, trade_date=run_date)
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await refresh_task
            await subscription.close()

    async def run_snapshot_polling(self, *, trade_date: date | None = None) -> None:
        run_date = trade_date or date.today()
        while True:
            last_error: str | None = None
            last_trigger: dict[str, Any] | None = None
            last_blocked_reason: str | None = None
            missing_ticks: list[str] = []
            try:
                async with self.session_factory() as session:
                    await unlock_t1_positions(session, trade_date=run_date)
                    self.positions_by_code = await load_guard_positions(session)
            except Exception:
                self.positions_by_code = {}
                last_error = "load_positions_failed"
                logger.exception(
                    "realtime risk guard failed to load positions",
                    extra={"trade_date": run_date.isoformat()},
                )

            ts_codes = sorted(self.positions_by_code)
            if ts_codes:
                try:
                    ticks = await EastMoneyRealtimeProvider(ts_codes).fetch_snapshot()
                except Exception as exc:
                    last_error = str(exc) or exc.__class__.__name__
                    logger.exception(
                        "realtime risk guard failed to fetch realtime snapshot",
                        extra={"trade_date": run_date.isoformat(), "ts_codes": ",".join(ts_codes)},
                    )
                else:
                    seen_codes = {tick.ts_code for tick in ticks}
                    missing_ticks = [code for code in ts_codes if code not in seen_codes]
                    if missing_ticks:
                        last_blocked_reason = "未返回实时行情"
                    for tick in ticks:
                        for position in self.positions_by_code.get(tick.ts_code, []):
                            preflight_reason = _preflight_blocked_reason(position, tick)
                            if preflight_reason:
                                last_blocked_reason = preflight_reason
                        try:
                            results = await self.handle_tick(tick, trade_date=run_date)
                        except Exception:
                            last_error = "handle_tick_failed"
                            logger.exception(
                                "realtime risk guard failed to handle tick",
                                extra={
                                    "trade_date": run_date.isoformat(),
                                    "ts_code": tick.ts_code,
                                    "tick_price": str(tick.price),
                                },
                            )
                        else:
                            for result in results or []:
                                reason = result.get("reason")
                                if result.get("order") is not None and result.get("action") != "HOLD":
                                    last_trigger = {
                                        "ts_code": tick.ts_code,
                                        "action": result.get("action"),
                                        "reason": reason,
                                        "price": tick.price,
                                    }
                                elif reason:
                                    last_blocked_reason = str(reason)

            try:
                async with self.session_factory() as session:
                    await write_risk_guard_heartbeat(
                        session,
                        refresh_interval_seconds=self.refresh_interval_seconds,
                        loaded_positions=len(_flatten_positions(self.positions_by_code)),
                        tracked_symbols=len(ts_codes),
                        trade_date=run_date,
                        last_error=last_error,
                        last_trigger=last_trigger,
                        last_blocked_reason=last_blocked_reason,
                        missing_ticks=missing_ticks,
                    )
            except Exception:
                logger.exception("realtime risk guard failed to write heartbeat")

            await asyncio.sleep(self.refresh_interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Subscribe realtime ticks and create sim stop-profit/stop-loss orders.")
    parser.add_argument("--trade-date", help="Trading date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--refresh-interval", type=float, default=30.0, help="Seconds between position subscription refreshes.")
    parser.add_argument(
        "--mode",
        choices=("redis", "snapshot"),
        default="snapshot",
        help="Use Redis tick subscription or EastMoney HTTP snapshot polling. Defaults to snapshot.",
    )
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date) if args.trade_date else None
    guard = RealtimeRiskGuard(refresh_interval_seconds=args.refresh_interval)
    if args.mode == "redis":
        asyncio.run(guard.run(trade_date=trade_date))
    else:
        asyncio.run(guard.run_snapshot_polling(trade_date=trade_date))


if __name__ == "__main__":
    main()
