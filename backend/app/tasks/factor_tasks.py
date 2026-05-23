"""Celery tasks for factor computation and IC/IR analysis."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from app.factor.service import analyze_factor_icir, compute_factors_for_date
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked


@celery_app.task(name="app.tasks.factor_tasks.compute_daily_factors", bind=True)
def compute_daily_factors(
    self,
    trade_date: str | None = None,
    scope_type: str = "all",
    scope_value: str | None = None,
) -> dict[str, Any]:
    if scope_type == "watchlist_group" and not scope_value:
        raise ValueError("scope_value is required for watchlist_group scope")
    if scope_type not in {"all", "watchlist_group"}:
        raise ValueError("scope_type must be 'all' or 'watchlist_group'")

    run_date = date.fromisoformat(trade_date) if trade_date else None
    return asyncio.run(
        _run_tracked(
            "compute_daily_factors",
            self.request.id,
            {"trade_date": run_date, "scope_type": scope_type, "scope_value": scope_value},
            lambda session: compute_factors_for_date(
                session,
                trade_date=run_date,
                scope_type=scope_type,
                scope_value=scope_value,
            ),
        )
    )


@celery_app.task(name="app.tasks.factor_tasks.analyze_factor_icir", bind=True)
def analyze_factor_icir_task(
    self,
    factor_name: str,
    period_start: str,
    period_end: str,
    forward_days: int = 5,
) -> dict[str, Any]:
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    return asyncio.run(
        _run_tracked(
            "analyze_factor_icir",
            self.request.id,
            {
                "factor_name": factor_name,
                "period_start": start,
                "period_end": end,
                "forward_days": forward_days,
            },
            lambda session: analyze_factor_icir(
                session,
                factor_name=factor_name,
                period_start=start,
                period_end=end,
                forward_days=forward_days,
            ),
        )
    )
