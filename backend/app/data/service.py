from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fetcher import DataProvider, default_providers, fetch_with_fallback
from app.data.repository import (
    create_alert,
    record_update_failure,
    record_update_success,
    upsert_daily_kline,
    upsert_stock_basic,
    upsert_trade_calendar,
)
from app.data.validators import validate_daily_kline, validate_stock_basic, validate_trade_calendar


def default_kline_window(today: date | None = None) -> tuple[date, date]:
    end_date = today or datetime.now(tz=UTC).date()
    return end_date - timedelta(days=365), end_date


async def select_sample_stock_codes(session: AsyncSession, limit: int = 20) -> list[str]:
    result = await session.execute(
        text(
            """
            SELECT ts_code
            FROM stock_basic
            WHERE is_delisted = FALSE
              AND symbol ~ '^[036][0-9]{5}$'
            ORDER BY symbol
            LIMIT :limit
            """
        ),
        {"limit": limit},
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
    provider_list = providers or default_providers()
    source, records = fetch_with_fallback(provider_list, "fetch_stock_basic")
    valid_records = []
    invalid_records = []
    for record in records:
        try:
            validate_stock_basic(record)
        except Exception as exc:
            invalid_records.append({"ts_code": record.ts_code, "error": str(exc)})
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
    return {"source": source, "inserted_or_updated": count, "skipped": len(invalid_records)}


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
    )
    for record in records:
        validate_trade_calendar(record)

    count = await upsert_trade_calendar(session, records)
    latest = max((record.cal_date for record in records if record.is_open), default=None)
    await record_update_success(session, "trade_calendar", source, last_trade_date=latest)
    await session.commit()
    return {"source": source, "inserted_or_updated": count, "start_date": start_date, "end_date": end_date}


async def sync_kline(
    session: AsyncSession,
    ts_codes: list[str] | None,
    start_date: date,
    end_date: date,
    providers: list[DataProvider] | None = None,
) -> dict[str, Any]:
    provider_list = providers or default_providers()
    codes = ts_codes or await select_sample_stock_codes(session)
    if not codes:
        await sync_stock_basic(session, provider_list)
        codes = await select_sample_stock_codes(session)
    if not codes:
        raise ValueError("no stock codes available after stock basic sync; pass ts_codes explicitly")

    total = 0
    failures: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}

    for ts_code in codes:
        try:
            source, records = fetch_with_fallback(
                provider_list,
                "fetch_daily_kline",
                ts_code,
                start_date,
                end_date,
            )
            for record in records:
                validate_daily_kline(record)
            count = await upsert_daily_kline(session, records)
            total += count
            source_counts[source] = source_counts.get(source, 0) + count
            latest = max((record.trade_date for record in records), default=None)
            await record_update_success(session, "daily_kline", source, ts_code=ts_code, last_trade_date=latest)
        except Exception as exc:
            message = str(exc)
            failures.append({"ts_code": ts_code, "error": message})
            await record_update_failure(session, "daily_kline", "fallback", message, ts_code=ts_code)

    if failures:
        await create_alert(
            session,
            level="warning" if total else "error",
            category="data_sync",
            title="Daily kline sync completed with failures",
            message=f"{len(failures)} symbols failed during kline sync",
            payload={"failures": failures[:20]},
        )

    await session.commit()
    if total == 0 and failures:
        raise RuntimeError(f"all kline sync attempts failed: {failures[0]['error']}")

    return {
        "requested_symbols": len(codes),
        "inserted_or_updated": total,
        "source_counts": source_counts,
        "failures": failures,
        "start_date": start_date,
        "end_date": end_date,
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
