from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.factor.service import compute_factors_for_date

logger = logging.getLogger(__name__)


async def backfill_factor_scores(
    session: AsyncSession,
    *,
    period_start: date,
    period_end: date,
    scope_type: str = "all",
    scope_value: str | None = None,
) -> dict[str, Any]:
    if scope_type not in {"all", "watchlist_group"}:
        raise ValueError("scope_type must be 'all' or 'watchlist_group'")
    if scope_type == "watchlist_group" and not scope_value:
        raise ValueError("scope_value is required for watchlist_group scope")

    result = await session.execute(
        text(
            """
            SELECT cal_date
            FROM trade_calendar
            WHERE cal_date BETWEEN :start AND :end
              AND is_open = TRUE
            ORDER BY cal_date
            """
        ),
        {"start": period_start, "end": period_end},
    )
    open_days = [row["cal_date"] for row in result.mappings().all()]

    if not open_days:
        return {
            "total_days": 0,
            "success_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "failures": [],
            "detail": "no open trading days in the specified range",
        }

    already_done: set[date] = set()
    done_check = await session.execute(
        text(
            """
            SELECT DISTINCT score_date
            FROM factor_score_runs
            WHERE score_date BETWEEN :start AND :end
              AND status = 'success'
              AND scope_type = :scope_type
              AND (
                    (:scope_type = 'all' AND scope_value IS NULL)
                 OR (:scope_type <> 'all' AND scope_value = :scope_value)
              )
            """
        ),
        {
            "start": period_start,
            "end": period_end,
            "scope_type": scope_type,
            "scope_value": scope_value if scope_type != "all" else None,
        },
    )
    for row in done_check.mappings().all():
        already_done.add(row["score_date"])

    per_day: list[dict[str, Any]] = []
    for day in open_days:
        if day in already_done:
            per_day.append({"trade_date": day.isoformat(), "status": "skipped", "reason": "already completed"})
            continue
        try:
            day_result = await compute_factors_for_date(
                session,
                trade_date=day,
                scope_type=scope_type,
                scope_value=scope_value,
            )
            if day_result.get("skipped"):
                per_day.append({"trade_date": day.isoformat(), "status": "skipped", "reason": day_result.get("reason", "non-trading day")})
            else:
                per_day.append({"trade_date": day.isoformat(), "status": "success", "detail": day_result})
        except Exception as exc:
            logger.exception("backfill failed for %s", day.isoformat())
            await session.rollback()
            per_day.append({"trade_date": day.isoformat(), "status": "failed", "reason": str(exc)})

    success_count = sum(1 for d in per_day if d["status"] == "success")
    skipped_count = sum(1 for d in per_day if d["status"] == "skipped")
    failed_count = sum(1 for d in per_day if d["status"] == "failed")

    return {
        "total_days": len(open_days),
        "success_count": success_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "failures": [d for d in per_day if d["status"] == "failed"],
        "per_day": per_day,
    }