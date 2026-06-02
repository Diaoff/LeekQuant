from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fetcher import DataProvider, default_providers, fetch_with_fallback, get_data_proxy_url, stock_basic_providers
from app.data.models import DailyKline
from app.data.repository import (
    backfill_stock_basic_market,
    create_alert,
    delete_unsupported_stock_data,
    record_update_failure,
    record_update_success,
    upsert_daily_kline,
    upsert_stock_basic,
    upsert_trade_calendar,
)
from app.data.stock_scope import SUPPORTED_STOCK_SQL_CONDITION, is_supported_stock_basic, supported_stock_sql_condition
from app.data.validators import validate_daily_kline, validate_stock_basic, validate_trade_calendar

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


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


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


async def _is_st_stock(session: AsyncSession, ts_code: str) -> bool:
    result = await session.execute(text("SELECT is_st FROM stock_basic WHERE ts_code = :ts_code"), {"ts_code": ts_code})
    return bool(result.scalar_one_or_none())


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
                (SELECT MAX(cal_date) FROM trade_calendar WHERE is_open = TRUE) AS latest_trade_calendar_date,
                (SELECT COUNT(*) FROM daily_kline) AS daily_kline_count,
                (SELECT MAX(trade_date) FROM daily_kline) AS latest_kline_trade_date
            """
        )
    )
    row = result.mappings().one()

    tasks_result = await session.execute(
        text(
            """
            SELECT id, task_name, task_id, status, started_at, finished_at, duration_ms, payload, result, error_message
            FROM task_runs
            ORDER BY started_at DESC
            LIMIT 10
            """
        )
    )
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
        "recent_tasks": [dict(item) for item in tasks_result.mappings().all()],
        "recent_alerts": [dict(item) for item in alerts_result.mappings().all()],
    }


async def sync_stock_basic(
    session: AsyncSession,
    providers: list[DataProvider] | None = None,
) -> dict[str, Any]:
    provider_list = providers or stock_basic_providers()
    source, records = fetch_with_fallback(provider_list, "fetch_stock_basic", proxy_url=get_data_proxy_url())
    valid_records = []
    invalid_records = []
    excluded_records = []
    for record in records:
        try:
            validate_stock_basic(record)
        except Exception as exc:
            invalid_records.append({"ts_code": record.ts_code, "error": str(exc)})
            continue
        if not is_supported_stock_basic(record):
            excluded_records.append({"ts_code": record.ts_code, "market": record.market, "exchange": record.exchange})
            continue
        valid_records.append(record)

    if not valid_records:
        message = "stock basic sync returned no valid records"
        await record_update_failure(session, "stock_basic", source, message)
        await create_alert(
            session,
            level="error",
            category="data_sync",
            title="Stock basic sync failed validation",
            message=message,
            payload={"invalid_records": invalid_records[:20]},
        )
        await session.commit()
        raise ValueError(message)

    count = await upsert_stock_basic(session, valid_records)
    await backfill_stock_basic_market(session)
    deleted = await delete_unsupported_stock_data(session)
    await record_update_success(session, "stock_basic", source)
    if invalid_records:
        await create_alert(
            session,
            level="warning",
            category="data_sync",
            title="Stock basic sync skipped invalid records",
            message=f"{len(invalid_records)} stock basic rows failed validation",
            payload={"invalid_records": invalid_records[:20]},
        )
    await session.commit()
    return {
        "source": source,
        "inserted_or_updated": count,
        "skipped": len(invalid_records) + len(excluded_records),
        "skipped_invalid": len(invalid_records),
        "skipped_excluded": len(excluded_records),
        "deleted_unsupported": deleted,
    }


async def sync_trade_calendar(
    session: AsyncSession,
    start_date: date,
    end_date: date,
    providers: list[DataProvider] | None = None,
) -> dict[str, Any]:
    source, records = fetch_with_fallback(
        providers or default_providers(),
        "fetch_trade_calendar",
        start_date,
        end_date,
        proxy_url=get_data_proxy_url(),
    )
    for record in records:
        validate_trade_calendar(record)

    count = await upsert_trade_calendar(session, records)
    latest = max((record.cal_date for record in records if record.is_open), default=None)
    await record_update_success(session, "trade_calendar", source, last_trade_date=latest)
    await session.commit()
    return {"source": source, "inserted_or_updated": count, "start_date": start_date, "end_date": end_date}


async def sync_kline(
    session: AsyncSession | None,
    ts_codes: list[str] | None,
    start_date: date,
    end_date: date,
    providers: list[DataProvider] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    commit_each: bool = False,
    concurrency: int = 1,
) -> dict[str, Any]:
    from app.db.session import async_session_factory as _per_stock_sf

    provider_list = providers or default_providers()
    concurrency = max(1, concurrency)
    if ts_codes is None:
        assert session is not None, "session required when ts_codes is None"
        codes = await select_sample_stock_codes(session)
        if not codes:
            await sync_stock_basic(session, provider_list)
            codes = await select_sample_stock_codes(session)
    else:
        codes = ts_codes
    if not codes:
        raise ValueError("no stock codes available after stock basic sync; pass ts_codes explicitly")

    total = 0
    completed = 0
    failures: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    progress_lock = asyncio.Lock()

    async def report_progress(ts_code: str) -> None:
        nonlocal completed
        if progress_callback is None:
            return
        async with progress_lock:
            completed += 1
            try:
                progress_callback(completed, len(codes), ts_code)
            except Exception:
                pass

    async def process_code(ts_code: str) -> dict[str, Any]:
        if commit_each:
            async with _per_stock_sf() as wk_session:
                try:
                    source, records = await asyncio.to_thread(
                        fetch_with_fallback,
                        providers or default_providers(),
                        "fetch_daily_kline",
                        ts_code,
                        start_date,
                        end_date,
                        proxy_url=get_data_proxy_url(),
                    )
                    for record in records:
                        validate_daily_kline(record)
                    quality_issues = _daily_kline_quality_issues(records, is_st=await _is_st_stock(wk_session, ts_code))
                    count = await upsert_daily_kline(wk_session, records)
                    latest = max((record.trade_date for record in records), default=None)
                    await record_update_success(wk_session, "daily_kline", source, ts_code=ts_code, last_trade_date=latest)
                    await _create_kline_quality_alert(
                        wk_session,
                        ts_code=ts_code,
                        source=source,
                        start_date=start_date,
                        end_date=end_date,
                        issues=quality_issues,
                    )
                    await wk_session.commit()
                    return {"ts_code": ts_code, "source": source, "count": count}
                except Exception as exc:
                    message = str(exc)
                    try:
                        await wk_session.rollback()
                    except Exception:
                        pass
                    await record_update_failure(wk_session, "daily_kline", "fallback", message, ts_code=ts_code)
                    await wk_session.commit()
                    return {"ts_code": ts_code, "error": message}

        try:
            source, records = await asyncio.to_thread(
                fetch_with_fallback,
                provider_list,
                "fetch_daily_kline",
                ts_code,
                start_date,
                end_date,
                proxy_url=get_data_proxy_url(),
            )
            for record in records:
                validate_daily_kline(record)
            assert session is not None
            quality_issues = _daily_kline_quality_issues(records, is_st=await _is_st_stock(session, ts_code))
            count = await upsert_daily_kline(session, records)
            latest = max((record.trade_date for record in records), default=None)
            await record_update_success(session, "daily_kline", source, ts_code=ts_code, last_trade_date=latest)
            await _create_kline_quality_alert(
                session,
                ts_code=ts_code,
                source=source,
                start_date=start_date,
                end_date=end_date,
                issues=quality_issues,
            )
            return {"ts_code": ts_code, "source": source, "count": count}
        except Exception as exc:
            message = str(exc)
            assert session is not None
            await record_update_failure(session, "daily_kline", "fallback", message, ts_code=ts_code)
            return {"ts_code": ts_code, "error": message}

    if commit_each and concurrency > 1:
        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_process(ts_code: str) -> dict[str, Any]:
            async with semaphore:
                result = await process_code(ts_code)
                await report_progress(ts_code)
                return result

        results = await asyncio.gather(*(bounded_process(ts_code) for ts_code in codes))
    else:
        results = []
        for ts_code in codes:
            result = await process_code(ts_code)
            results.append(result)
            await report_progress(ts_code)

    for result in results:
        if "error" in result:
            failures.append({"ts_code": result["ts_code"], "error": result["error"]})
            continue
        count = int(result["count"])
        total += count
        source = str(result["source"])
        source_counts[source] = source_counts.get(source, 0) + count

    if failures:
        if session is not None:
            await create_alert(
                session,
                level="warning" if total else "error",
                category="data_sync",
                title="Daily kline sync completed with failures",
                message=f"{len(failures)} symbols failed during kline sync",
                payload={
                    "start_date": start_date,
                    "end_date": end_date,
                    "failure_count": len(failures),
                    "failures": failures[:20],
                },
            )
            if commit_each:
                await session.commit()
        else:
            async with _per_stock_sf() as alert_session:
                await create_alert(
                    alert_session,
                    level="warning" if total else "error",
                    category="data_sync",
                    title="Daily kline sync completed with failures",
                    message=f"{len(failures)} symbols failed during kline sync",
                    payload={
                        "start_date": start_date,
                        "end_date": end_date,
                        "failure_count": len(failures),
                        "failures": failures[:20],
                    },
                )
                await alert_session.commit()

    if not commit_each:
        assert session is not None
        await session.commit()
    if total == 0 and failures:
        raise RuntimeError(f"all kline sync attempts failed: {failures[0]['error']}")

    return {
        "requested_symbols": len(codes),
        "inserted_or_updated": total,
        "source_counts": source_counts,
        "failures": failures,
    }


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
        text("SELECT MAX(cal_date) FROM trade_calendar WHERE is_open = TRUE")
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

    latest_open_result = await session.execute(text("SELECT MAX(cal_date) FROM trade_calendar WHERE is_open = TRUE"))
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

    result = await session.execute(
        text(
            f"""
            WITH latest_kline AS (
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
                    ELSE (
                        SELECT MIN(tc.cal_date)
                        FROM trade_calendar tc
                        WHERE tc.is_open = TRUE
                          AND tc.cal_date > lk.last_trade_date
                          AND tc.cal_date <= :end_date
                    )
                END AS start_date,
                :end_date AS end_date
            FROM stock_basic sb
            LEFT JOIN latest_kline lk ON lk.ts_code = sb.ts_code
            WHERE sb.is_delisted = FALSE
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
