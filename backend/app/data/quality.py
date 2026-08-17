"""Daily kline data-quality checks and alert creation."""

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

def _daily_kline_quality_issues(records: list[DailyKline], *, is_st: bool = False) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    limit_pct = ST_PRICE_LIMIT if is_st else MAIN_BOARD_PRICE_LIMIT
    for record in records:
        if record.is_suspended:
            continue
        if record.adj_factor is None:
            issues.append(
                {
                    "type": "missing_adj_factor",
                    "ts_code": record.ts_code,
                    "trade_date": record.trade_date,
                    "source": record.data_source,
                    "reason": "adj_factor is missing on non-suspended daily kline",
                }
            )
        close = _as_decimal(record.close)
        pre_close = _as_decimal(record.pre_close)
        if pre_close is None or pre_close == Decimal("0") or close is None:
            continue
        change_pct = (close - pre_close) / pre_close
        if abs(change_pct) > limit_pct + PRICE_LIMIT_TOLERANCE:
            issues.append(
                {
                    "type": "abnormal_price_change",
                    "ts_code": record.ts_code,
                    "trade_date": record.trade_date,
                    "source": record.data_source,
                    "close": record.close,
                    "pre_close": record.pre_close,
                    "change_pct": change_pct,
                    "limit_pct": limit_pct,
                    "reason": "close/pre_close change exceeds A-share price limit threshold",
                }
            )
    return issues

async def _create_kline_quality_alert(
    session: AsyncSession,
    *,
    ts_code: str,
    source: str,
    start_date: date,
    end_date: date,
    issues: list[dict[str, Any]],
) -> None:
    if not issues:
        return
    counts: dict[str, int] = {}
    for issue in issues:
        issue_type = str(issue["type"])
        counts[issue_type] = counts.get(issue_type, 0) + 1
    await create_alert(
        session,
        level="warning",
        category="data_quality",
        title="Daily kline data quality warnings",
        message=f"{ts_code} has {len(issues)} data quality warnings during kline sync",
        payload={
            "ts_code": ts_code,
            "source": source,
            "start_date": start_date,
            "end_date": end_date,
            "counts": counts,
            "issues": issues[:20],
        },
    )

async def _bulk_load_is_st(session: AsyncSession, ts_codes: list[str]) -> dict[str, bool]:
    """Load is_st flag for all ts_codes in ONE query instead of N.

    For sync_kline processing 4000+ stocks, this collapses 4000 DB round-trips
    to 1. Returns ``{ts_code: bool}``; missing stocks default to False.
    """
    if not ts_codes:
        return {}
    result = await session.execute(
        text(
            "SELECT ts_code, COALESCE(is_st, FALSE) AS is_st "
            "FROM stock_basic "
            "WHERE ts_code = ANY(CAST(:ts_codes AS VARCHAR[]))"
        ),
        {"ts_codes": ts_codes},
    )
    rows = result.all()
    present = {row.ts_code: bool(row.is_st) for row in rows}
    return {code: present.get(code, False) for code in ts_codes}
