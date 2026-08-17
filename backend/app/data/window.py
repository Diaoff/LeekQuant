"""Incremental / full kline sync window inference."""

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

PRICE_LIMIT_TOLERANCE = Decimal("0.0005")
MAIN_BOARD_PRICE_LIMIT = Decimal("0.10")
ST_PRICE_LIMIT = Decimal("0.05")

from app.data.sample import default_kline_window

async def infer_incremental_kline_window(session: AsyncSession) -> tuple[date | None, date | None]:
    max_kline_result = await session.execute(text("SELECT MAX(trade_date) FROM daily_kline"))
    last_kline_date = max_kline_result.scalar_one_or_none()
    if last_kline_date is None:
        return default_kline_window()

    next_open_result = await session.execute(
        text(
            """
            SELECT MIN(cal_date)
            FROM trade_calendar
            WHERE is_open = TRUE AND cal_date > :last_kline_date
            """
        ),
        {"last_kline_date": last_kline_date},
    )
    start_date = next_open_result.scalar_one_or_none()
    latest_open_result = await session.execute(
        text("SELECT MAX(cal_date) FROM trade_calendar WHERE is_open = TRUE AND cal_date <= CURRENT_DATE")
    )
    end_date = latest_open_result.scalar_one_or_none()
    if start_date is None or end_date is None or start_date > end_date:
        return None, None
    return start_date, end_date

async def infer_incremental_kline_ranges(
    session: AsyncSession,
    *,
    ts_codes: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if ts_codes is not None and not ts_codes:
        return []

    latest_open_result = await session.execute(text("SELECT MAX(cal_date) FROM trade_calendar WHERE is_open = TRUE AND cal_date <= CURRENT_DATE"))
    end_date = latest_open_result.scalar_one_or_none()
    if end_date is None:
        return []

    default_start, _default_end = default_kline_window()
    code_filter = ""
    limit_clause = ""
    params: dict[str, Any] = {"end_date": end_date, "default_start": default_start}
    if ts_codes is not None:
        code_filter = "AND sb.ts_code = ANY(CAST(:ts_codes AS VARCHAR[]))"
        params["ts_codes"] = ts_codes
    if limit is not None:
        limit_clause = "LIMIT :limit"
        params["limit"] = max(0, limit)

    # Optimized query: pre-compute the next-open-day lookup with LEAD() window
    # function (single pass over trade_calendar) instead of correlated subquery
    # per stock. The CASE handles stocks with no K-line data separately.
    result = await session.execute(
        text(
            f"""
            WITH next_open AS (
                SELECT cal_date,
                       LEAD(cal_date) OVER (ORDER BY cal_date) AS next_cal_date
                FROM trade_calendar
                WHERE is_open = TRUE
            ),
            latest_kline AS (
                SELECT ts_code, MAX(trade_date) AS last_trade_date
                FROM daily_kline
                GROUP BY ts_code
            )
            SELECT
                sb.ts_code,
                lk.last_trade_date,
                CASE
                    WHEN lk.last_trade_date IS NULL THEN (
                        SELECT MIN(tc.cal_date)
                        FROM trade_calendar tc
                        WHERE tc.is_open = TRUE
                          AND tc.cal_date >= COALESCE(sb.list_date, :default_start)
                          AND tc.cal_date <= :end_date
                    )
                    ELSE no.next_cal_date
                END AS start_date,
                :end_date AS end_date
            FROM stock_basic sb
            LEFT JOIN latest_kline lk ON lk.ts_code = sb.ts_code
            LEFT JOIN next_open no ON no.cal_date = lk.last_trade_date
            WHERE sb.is_delisted = FALSE
              AND (sb.delist_date IS NULL OR sb.delist_date > :end_date)
              AND {supported_stock_sql_condition("sb")}
              {code_filter}
            ORDER BY sb.symbol
            {limit_clause}
            """
        ),
        params,
    )
    ranges = []
    for row in result.mappings().all():
        start_date = row["start_date"]
        if start_date is None or start_date > end_date:
            continue
        ranges.append(
            {
                "ts_code": row["ts_code"],
                "start_date": start_date,
                "end_date": end_date,
                "last_trade_date": row["last_trade_date"],
            }
        )
    return ranges

def split_kline_ranges_by_year(ranges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split any range spanning multiple years into per-year sub-ranges.

    Replaces the 400-day lookback clamp (which created data gaps for
    long-suspended stocks). Instead of clamping, we split a multi-year gap
    into year-bounded chunks so each chunk is small enough to fit in a
    batch_size=20 group without hitting the soft time limit.

    Example: ts_code=X with start_date=2024-03-15, end_date=2026-07-21
    becomes 3 ranges:
      (2024-03-15, 2024-12-31)
      (2025-01-01, 2025-12-31)
      (2026-01-01, 2026-07-21)
    """
    split: list[dict[str, Any]] = []
    for r in ranges:
        start: date = r["start_date"]
        end: date = r["end_date"]
        if start.year == end.year:
            split.append(r)
            continue
        # Multi-year: emit one range per calendar year boundary
        current = start
        while current.year < end.year:
            year_end = date(current.year, 12, 31)
            split.append({**r, "start_date": current, "end_date": year_end})
            current = date(current.year + 1, 1, 1)
        split.append({**r, "start_date": current, "end_date": end})
    return split

async def infer_full_kline_ranges(
    session: AsyncSession,
    *,
    ts_codes: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Compute per-stock date ranges for a TRUE full-history (全量) K-line sync.

    Unlike the incremental range inference (which only fills gaps and clamps
    lookback to avoid huge first loads), the full sync starts every stock at its
    ``list_date`` (or the earliest available open trading day) and runs through
    the latest open trading day — i.e. the entire listed history. The result is
    sliced by the caller (``kline_sync_dispatch``) via ``split_kline_ranges_by_year``,
    exactly like the incremental path, so the job never trips the global Celery
    time limit.
    """
    latest_open_result = await session.execute(
        text("SELECT MAX(cal_date) FROM trade_calendar WHERE is_open = TRUE AND cal_date <= CURRENT_DATE")
    )
    end_date = latest_open_result.scalar_one_or_none()
    if end_date is None:
        return []

    params: dict[str, Any] = {"end_date": end_date}
    code_filter = ""
    limit_clause = ""
    if ts_codes is not None:
        if not ts_codes:
            return []
        code_filter = "AND sb.ts_code = ANY(CAST(:ts_codes AS VARCHAR[]))"
        params["ts_codes"] = ts_codes
    if limit is not None:
        limit_clause = "LIMIT :limit"
        params["limit"] = max(0, limit)

    result = await session.execute(
        text(
            f"""
            SELECT
                sb.ts_code,
                CASE
                    WHEN sb.list_date IS NOT NULL AND sb.list_date <= :end_date
                        THEN sb.list_date
                    ELSE (
                        SELECT MIN(tc.cal_date)
                        FROM trade_calendar tc
                        WHERE tc.is_open = TRUE AND tc.cal_date <= :end_date
                    )
                END AS start_date,
                :end_date AS end_date
            FROM stock_basic sb
            WHERE sb.is_delisted = FALSE
              AND (sb.delist_date IS NULL OR sb.delist_date > :end_date)
              AND {supported_stock_sql_condition("sb")}
              {code_filter}
            ORDER BY sb.symbol
            {limit_clause}
            """
        ),
        params,
    )
    ranges = []
    for row in result.mappings().all():
        start_date = row["start_date"]
        if start_date is None or start_date > end_date:
            continue
        ranges.append(
            {
                "ts_code": row["ts_code"],
                "start_date": start_date,
                "end_date": end_date,
                "last_trade_date": row.get("last_trade_date"),
            }
        )
    return ranges
