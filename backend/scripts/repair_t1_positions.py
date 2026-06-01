"""Repair simulation T+1 sellable shares.

Usage:
    python backend/scripts/repair_t1_positions.py
    python backend/scripts/repair_t1_positions.py --trade-date 2026-06-01
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import async_session_factory  # noqa: E402
from app.sim.service import unlock_t1_positions  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair locked T+1 simulation positions.")
    parser.add_argument(
        "--trade-date",
        type=date.fromisoformat,
        default=None,
        help="Trading date used for T+1 calculation, YYYY-MM-DD. Defaults to latest open trading day <= today.",
    )
    return parser.parse_args()


async def resolve_trade_date(explicit: date | None) -> date:
    if explicit is not None:
        return explicit
    async with async_session_factory() as session:
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
        row = result.mappings().one_or_none()
        if row is None:
            raise RuntimeError("No open trading day found in trade_calendar")
        return row["cal_date"]


async def locked_position_summary(trade_date: date) -> list[dict[str, Any]]:
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                """
                WITH today_buys AS (
                    SELECT account_id, ts_code, SUM(volume)::INTEGER AS volume
                    FROM sim_trades
                    WHERE direction = '买入' AND trade_time::DATE = :trade_date
                    GROUP BY account_id, ts_code
                ),
                expected AS (
                    SELECT p.account_id, p.ts_code, p.shares, p.available_shares, p.frozen_shares,
                           GREATEST(0, p.shares - p.frozen_shares - COALESCE(tb.volume, 0))::INTEGER AS expected_available
                    FROM sim_positions p
                    LEFT JOIN today_buys tb
                      ON tb.account_id = p.account_id AND tb.ts_code = p.ts_code
                    WHERE p.shares > 0
                )
                SELECT account_id, ts_code, shares, available_shares, frozen_shares, expected_available
                FROM expected
                WHERE available_shares <> expected_available
                ORDER BY account_id, ts_code
                """
            ),
            {"trade_date": trade_date},
        )
        return [dict(row) for row in result.mappings().all()]


def print_summary(label: str, rows: list[dict[str, Any]]) -> None:
    print(f"{label}: {len(rows)} position(s)")
    for row in rows[:20]:
        print(
            "  "
            f"account={row['account_id']} ts_code={row['ts_code']} "
            f"shares={row['shares']} available={row['available_shares']} "
            f"frozen={row['frozen_shares']} expected={row['expected_available']}"
        )
    if len(rows) > 20:
        print(f"  ... {len(rows) - 20} more")


async def main() -> int:
    args = parse_args()
    trade_date = await resolve_trade_date(args.trade_date)
    print(f"repair trade_date={trade_date.isoformat()}")

    before = await locked_position_summary(trade_date)
    print_summary("before", before)

    async with async_session_factory() as session:
        updated = await unlock_t1_positions(session, trade_date=trade_date)

    after = await locked_position_summary(trade_date)
    print(f"updated={updated}")
    print_summary("after", after)
    return 0 if not after else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
