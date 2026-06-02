"""Celery tasks for simulation trading maintenance."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import text

from app.realtime.risk_guard import RealtimeRiskGuard
from app.sim.service import match_order, snapshot_daily_nav, unlock_t1_positions
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked

A_SHARE_REALTIME_WINDOWS = (
    (time(9, 25), time(11, 30)),
    (time(13, 0), time(15, 0)),
)


def _is_realtime_trading_time(now: datetime | None = None) -> bool:
    current = now or datetime.now()
    current_time = current.time()
    return any(start <= current_time <= end for start, end in A_SHARE_REALTIME_WINDOWS)


async def _is_open_trade_day(session, run_date: date) -> bool:
    result = await session.execute(
        text("SELECT is_open FROM trade_calendar WHERE cal_date = :run_date"),
        {"run_date": run_date},
    )
    row = result.mappings().one_or_none()
    return bool(row and row["is_open"])


@celery_app.task(name="app.tasks.trading_tasks.unlock_t1_daily", bind=True)
def unlock_t1_daily(self, trade_date: str | None = None) -> dict[str, Any]:
    run_date = date.fromisoformat(trade_date) if trade_date else date.today()
    return asyncio.run(
        _run_tracked(
            "unlock_t1_daily",
            self.request.id,
            {"trade_date": run_date},
            lambda session: _unlock_t1_daily(session, run_date),
        )
    )


async def _unlock_t1_daily(session, run_date: date) -> dict[str, Any]:
    updated = await unlock_t1_positions(session, trade_date=run_date)
    return {"trade_date": run_date.isoformat(), "positions_updated": updated}


@celery_app.task(name="app.tasks.trading_tasks.snapshot_nav_daily", bind=True)
def snapshot_nav_daily(self, nav_date: str | None = None) -> dict[str, Any]:
    run_date = date.fromisoformat(nav_date) if nav_date else date.today()
    return asyncio.run(
        _run_tracked(
            "snapshot_nav_daily",
            self.request.id,
            {"nav_date": run_date},
            lambda session: _snapshot_nav_daily(session, run_date),
        )
    )


async def _snapshot_nav_daily(session, run_date: date) -> dict[str, Any]:
    calendar_result = await session.execute(
        text("SELECT is_open FROM trade_calendar WHERE cal_date = :run_date"),
        {"run_date": run_date},
    )
    calendar = calendar_result.mappings().one_or_none()
    if not calendar or not calendar["is_open"]:
        return {"nav_date": run_date.isoformat(), "skipped": True, "reason": "non-trading day"}

    result = await session.execute(
        text("SELECT id FROM sim_accounts WHERE status = 'active' ORDER BY id")
    )
    account_ids = [int(row["id"]) for row in result.mappings().all()]
    snapshots = []
    for account_id in account_ids:
        snapshots.append(await snapshot_daily_nav(session, account_id=account_id, nav_date=run_date))
    return {"nav_date": run_date.isoformat(), "snapshot_count": len(snapshots)}


@celery_app.task(name="app.tasks.trading_tasks.match_pending_orders", bind=True)
def match_pending_orders(self, trade_date: str | None = None, match_mode: str = "close") -> dict[str, Any]:
    run_date = date.fromisoformat(trade_date) if trade_date else date.today()
    return asyncio.run(
        _run_tracked(
            "match_pending_orders",
            self.request.id,
            {"trade_date": run_date, "match_mode": match_mode},
            lambda session: _match_pending_orders(session, run_date, match_mode),
        )
    )


async def _match_pending_orders(session, run_date: date, match_mode: str = "close") -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT o.id, a.user_id
            FROM sim_orders o
            JOIN sim_accounts a ON a.id = o.account_id
            WHERE o.status = '待成交'
            ORDER BY o.submit_time, o.id
            """
        )
    )
    rows = [dict(row) for row in result.mappings().all()]
    matched = 0
    pending = 0
    failed: list[dict[str, Any]] = []
    for row in rows:
        try:
            match_result = await match_order(
                session,
                user_id=int(row["user_id"]),
                order_id=int(row["id"]),
                trade_date=run_date,
                match_mode=match_mode,
            )
            if match_result.get("matched", match_result.get("status") == "全部成交"):
                matched += 1
            else:
                pending += 1
        except Exception as exc:
            failed.append({"order_id": row["id"], "error": str(exc)})
    return {"trade_date": run_date.isoformat(), "match_mode": match_mode, "matched": matched, "pending": pending, "failed": failed}


@celery_app.task(name="app.tasks.trading_tasks.realtime_risk_guard", bind=True)
def realtime_risk_guard(
    self,
    trade_date: str | None = None,
    refresh_interval_seconds: float = 30.0,
    mode: str = "snapshot",
) -> dict[str, Any]:
    run_date = date.fromisoformat(trade_date) if trade_date else date.today()

    async def run_guard() -> dict[str, Any]:
        guard = RealtimeRiskGuard(refresh_interval_seconds=refresh_interval_seconds)
        async with guard.session_factory() as session:
            if not await _is_open_trade_day(session, run_date):
                return {"trade_date": run_date.isoformat(), "status": "skipped", "reason": "non-trading day"}
        if not _is_realtime_trading_time():
            return {"trade_date": run_date.isoformat(), "status": "skipped", "reason": "outside realtime trading hours"}
        if mode == "redis":
            await guard.run(trade_date=run_date)
        else:
            await guard.run_snapshot_polling(trade_date=run_date)
        return {"trade_date": run_date.isoformat(), "status": "stopped"}

    return asyncio.run(run_guard())
