"""Stock sampling helpers for kline sync."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, AsyncContextManager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.convert import _as_decimal
from app.data.fetcher import DataProvider, DataProviderError, default_providers, fetch_union, fetch_with_fallback, filter_open_circuits, get_data_proxy_url, stock_basic_providers
from app.data.models import DailyKline
from app.data.repository import (
    backfill_stock_basic_market,
    create_alert,
    delete_unsupported_stock_data,
    list_recent_jobs,
    upsert_daily_kline,
    upsert_stock_basic,
    upsert_trade_calendar,
)
from app.data.stock_scope import SUPPORTED_STOCK_SQL_CONDITION, is_supported_stock_basic, supported_stock_sql_condition
from app.data.validators import validate_daily_kline, validate_stock_basic, validate_trade_calendar

logger = logging.getLogger(__name__)

SAMPLE_STOCK_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sz_main", ("000", "001")),
    ("sz_sme", ("002",)),
    ("chinext", ("300", "301")),
    ("sh_main", ("600", "601")),
    ("sh_secondary", ("603", "605")),
)

PRICE_LIMIT_TOLERANCE = Decimal("0.0005")
MAIN_BOARD_PRICE_LIMIT = Decimal("0.10")
ST_PRICE_LIMIT = Decimal("0.05")

def default_kline_window(today: date | None = None) -> tuple[date, date]:
    end_date = today or datetime.now(tz=UTC).date()
    return end_date - timedelta(days=365), end_date

def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (TypeError, KeyError):
        try:
            return row[index]
        except IndexError:
            return None

def _sample_bucket(symbol: str) -> str:
    for bucket, prefixes in SAMPLE_STOCK_BUCKETS:
        if symbol.startswith(prefixes):
            return bucket
    return "other"

def _balanced_sample_stock_codes(rows: list[Any], limit: int) -> list[str]:
    limit = max(limit, 0)
    if limit == 0:
        return []

    buckets: dict[str, list[tuple[str, str]]] = {bucket: [] for bucket, _ in SAMPLE_STOCK_BUCKETS}
    buckets["other"] = []

    for row in rows:
        ts_code = str(_row_value(row, "ts_code", 0))
        symbol = str(_row_value(row, "symbol", 1) or ts_code.split(".", 1)[0])
        buckets[_sample_bucket(symbol)].append((ts_code, symbol))

    selected: list[str] = []
    seen: set[str] = set()
    bucket_order = [bucket for bucket, _ in SAMPLE_STOCK_BUCKETS] + ["other"]
    max_bucket_size = max((len(buckets[bucket]) for bucket in bucket_order), default=0)
    for index in range(max_bucket_size):
        for bucket in bucket_order:
            if index >= len(buckets[bucket]):
                continue
            ts_code, _symbol = buckets[bucket][index]
            if ts_code in seen:
                continue
            selected.append(ts_code)
            seen.add(ts_code)
            if len(selected) >= limit:
                return selected

    return selected

async def select_sample_stock_codes(session: AsyncSession, limit: int = 20) -> list[str]:
    result = await session.execute(
        text(
            """
            SELECT ts_code, symbol
            FROM stock_basic
            WHERE is_delisted = FALSE
              AND symbol ~ '^[036][0-9]{5}$'
              AND """ + SUPPORTED_STOCK_SQL_CONDITION + """
            ORDER BY symbol
            """
        ),
    )
    return _balanced_sample_stock_codes(result.all(), limit)

async def select_all_stock_codes(session: AsyncSession) -> list[str]:
    result = await session.execute(
        text(
            "SELECT ts_code FROM stock_basic WHERE is_delisted = FALSE AND "
            + SUPPORTED_STOCK_SQL_CONDITION
            + " ORDER BY symbol"
        )
    )
    return [row[0] for row in result.all()]

async def get_data_status(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT
                (SELECT COUNT(*) FROM stock_basic) AS stock_basic_count,
                (SELECT COUNT(*) FROM trade_calendar) AS trade_calendar_count,
                (SELECT MAX(cal_date) FROM trade_calendar WHERE is_open = TRUE AND cal_date <= CURRENT_DATE) AS latest_trade_calendar_date,
                (SELECT COUNT(*) FROM daily_kline) AS daily_kline_count,
                (SELECT MAX(trade_date) FROM daily_kline) AS latest_kline_trade_date
            """
        )
    )
    row = result.mappings().one()

    # Recent non-K-line tasks from task_runs (fundamentals, factors, etc.).
    # K-line sync tasks now live in kline_sync_jobs — exclude legacy batch
    # tasks here so they don't appear twice. Keep kline_sync_dispatch visible
    # so the user sees the task was submitted even before the job is created.
    tasks_result = await session.execute(
        text(
            """
            SELECT id, task_name, task_id, status, started_at, finished_at, duration_ms, payload, result, error_message
            FROM task_runs
            WHERE task_name NOT IN (
                'incremental_kline_batch', 'full_kline_batch',
                'incremental_kline_update', 'sync_all_kline',
                'app.tasks.data_tasks.reconcile_kline_batches'
            )
            ORDER BY started_at DESC NULLS LAST, id DESC
            LIMIT 20
            """
        )
    )
    recent_tasks: list[dict[str, Any]] = [dict(item) for item in tasks_result.mappings().all()]

    # Recent K-line sync jobs from kline_sync_jobs (with progress from items).
    kline_jobs = await list_recent_jobs(session, limit=20)
    for job in kline_jobs:
        recent_tasks.append(
            {
                "id": job["id"],
                "task_name": f"kline_sync_{job['job_type']}",
                "task_id": None,
                "status": job["status"],
                "started_at": job.get("started_at"),
                "finished_at": job.get("completed_at"),
                "duration_ms": None,
                "payload": job.get("config"),
                "result": {
                    "scope_total": int(job.get("scope_total") or 0),
                    "scope_done": int(job.get("scope_done") or 0),
                    "scope_failed": int(job.get("scope_failed") or 0),
                    "permanent_failure_codes": job.get("permanent_failure_codes") or [],
                    "item_total": int(job.get("item_total") or 0),
                    "pending": int(job.get("pending") or 0),
                    "running": int(job.get("running") or 0),
                    "done": int(job.get("done") or 0),
                    "permanently_failed": int(job.get("permanently_failed") or 0),
                },
                "error_message": job.get("error"),
            }
        )

    # Sort the merged list by started_at DESC (NULLS LAST), then id DESC.
    recent_tasks.sort(
        key=lambda t: (t.get("started_at") is not None, t.get("started_at") or datetime.min.replace(tzinfo=UTC)),
        reverse=True,
    )
    recent_tasks = recent_tasks[:20]

    alerts_result = await session.execute(
        text(
            """
            SELECT id, level, category, title, message, created_at, is_resolved
            FROM alert_events
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
    )

    return {
        "stock_basic_count": row["stock_basic_count"],
        "trade_calendar_count": row["trade_calendar_count"],
        "latest_trade_calendar_date": row["latest_trade_calendar_date"],
        "daily_kline_count": row["daily_kline_count"],
        "latest_kline_trade_date": row["latest_kline_trade_date"],
        "recent_tasks": recent_tasks,
        "recent_alerts": [dict(item) for item in alerts_result.mappings().all()],
    }
