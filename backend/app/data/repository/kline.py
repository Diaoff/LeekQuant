from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import DailyKline
from app.data.stock_scope import supported_stock_sql_condition


async def upsert_daily_kline(session: AsyncSession, records: list[DailyKline]) -> int:
    if not records:
        return 0
    values = []
    for record in records:
        value = asdict(record)
        value["raw_payload"] = json.dumps(value["raw_payload"], ensure_ascii=False, default=str)
        values.append(value)
    await session.execute(
        text(
            """
            INSERT INTO daily_kline (
                ts_code, trade_date, open, high, low, close, pre_close, volume, amount,
                turnover_rate, adj_factor, is_suspended, is_limit_up, is_limit_down,
                data_source, raw_payload, updated_at
            )
            VALUES (
                :ts_code, :trade_date, :open, :high, :low, :close, :pre_close, :volume, :amount,
                :turnover_rate, :adj_factor, :is_suspended, :is_limit_up, :is_limit_down,
                :data_source, CAST(:raw_payload AS JSONB), NOW()
            )
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                open = COALESCE(EXCLUDED.open, daily_kline.open),
                high = COALESCE(EXCLUDED.high, daily_kline.high),
                low = COALESCE(EXCLUDED.low, daily_kline.low),
                close = COALESCE(EXCLUDED.close, daily_kline.close),
                pre_close = COALESCE(EXCLUDED.pre_close, daily_kline.pre_close),
                volume = COALESCE(EXCLUDED.volume, daily_kline.volume),
                amount = COALESCE(EXCLUDED.amount, daily_kline.amount),
                turnover_rate = COALESCE(EXCLUDED.turnover_rate, daily_kline.turnover_rate),
                adj_factor = COALESCE(EXCLUDED.adj_factor, daily_kline.adj_factor),
                is_suspended = COALESCE(EXCLUDED.is_suspended, daily_kline.is_suspended),
                is_limit_up = COALESCE(EXCLUDED.is_limit_up, daily_kline.is_limit_up),
                is_limit_down = COALESCE(EXCLUDED.is_limit_down, daily_kline.is_limit_down),
                data_source = COALESCE(EXCLUDED.data_source, daily_kline.data_source),
                raw_payload = COALESCE(EXCLUDED.raw_payload, daily_kline.raw_payload),
                updated_at = NOW()
            """
        ),
        values,
    )
    return len(records)


async def get_active_stock_codes(
    session: AsyncSession,
    ts_codes: list[str],
) -> list[str]:
    """Filter ``ts_codes`` down to stocks NOT suspended on their latest K-line day.

    Stocks with no K-line data at all are kept (we cannot know their status,
    and they need an initial sync anyway).
    """
    if not ts_codes:
        return []
    result = await session.execute(
        text(
            """
            WITH latest_k AS (
                SELECT DISTINCT ON (ts_code) ts_code, is_suspended
                FROM daily_kline
                WHERE ts_code = ANY(CAST(:ts_codes AS VARCHAR[]))
                ORDER BY ts_code, trade_date DESC
            )
            SELECT c.code
            FROM UNNEST(CAST(:ts_codes AS VARCHAR[])) AS c(code)
            LEFT JOIN latest_k lk ON lk.ts_code = c.code
            WHERE COALESCE(lk.is_suspended, FALSE) = FALSE
            ORDER BY c.code
            """
        ),
        {"ts_codes": ts_codes},
    )
    return [str(row[0]) for row in result.fetchall()]


async def get_sync_progress(
    session: AsyncSession,
    *,
    ts_codes: list[str] | None = None,
    watchlist_id: int | None = None,
) -> dict[str, Any]:
    """Report K-line sync progress straight from ``daily_kline`` (source of truth).

    A stock is "caught up" when ``MAX(daily_kline.trade_date)`` reaches the
    latest open trading day. Deliberately independent of ``data_update_state``
    (multiple rows per stock, per-source semantics) and of any Celery task
    status. ``total`` excludes delisted stocks and unsupported markets — the
    same scope used by ``infer_incremental_kline_ranges`` — so the progress
    denominator always matches what a dispatch would actually sync.

    Returns ``{latest_open_day, total, caught_up, remaining, not_caught_up_codes}``.
    No failure counts here: permanent failures live in ``kline_sync_jobs``.
    """
    params: dict[str, Any] = {
        "has_ts_codes": ts_codes is not None,
        "ts_codes": ts_codes or [],
        "has_watchlist": watchlist_id is not None,
        "watchlist_id": watchlist_id if watchlist_id is not None else -1,
    }
    result = await session.execute(
        text(
            f"""
            WITH latest AS (
                SELECT MAX(cal_date) AS latest_open_day
                FROM trade_calendar
                WHERE is_open = TRUE AND cal_date <= CURRENT_DATE
            ),
            scope AS (
                SELECT sb.ts_code
                FROM stock_basic sb
                CROSS JOIN latest l
                WHERE sb.is_delisted = FALSE
                  AND (sb.delist_date IS NULL OR sb.delist_date > l.latest_open_day)
                  AND {supported_stock_sql_condition("sb")}
                  AND (
                      NOT CAST(:has_ts_codes AS BOOLEAN)
                      OR sb.ts_code = ANY(CAST(:ts_codes AS VARCHAR[]))
                  )
                  AND (
                      NOT CAST(:has_watchlist AS BOOLEAN)
                      OR EXISTS (
                          SELECT 1
                          FROM watchlist w
                          JOIN watchlist_groups wg
                            ON wg.user_id = w.user_id AND wg.group_name = w.group_name
                          WHERE wg.id = :watchlist_id AND w.ts_code = sb.ts_code
                      )
                  )
            ),
            dk AS (
                SELECT ts_code, MAX(trade_date) AS last_kline_date
                FROM daily_kline
                WHERE ts_code IN (SELECT ts_code FROM scope)
                GROUP BY ts_code
            )
            SELECT
                (SELECT latest_open_day FROM latest) AS latest_open_day,
                COUNT(s.ts_code)::INT AS total,
                COUNT(*) FILTER (
                    WHERE dk.last_kline_date >= (SELECT latest_open_day FROM latest)
                )::INT AS caught_up,
                COUNT(*) FILTER (
                    WHERE dk.last_kline_date IS NULL
                       OR dk.last_kline_date < (SELECT latest_open_day FROM latest)
                )::INT AS remaining,
                COALESCE(
                    ARRAY_AGG(s.ts_code ORDER BY s.ts_code) FILTER (
                        WHERE dk.last_kline_date IS NULL
                           OR dk.last_kline_date < (SELECT latest_open_day FROM latest)
                    ),
                    ARRAY[]::VARCHAR[]
                ) AS not_caught_up_codes
            FROM scope s
            LEFT JOIN dk ON dk.ts_code = s.ts_code
            """
        ),
        params,
    )
    row = result.mappings().one()
    return {
        "latest_open_day": row["latest_open_day"],
        "total": int(row["total"] or 0),
        "caught_up": int(row["caught_up"] or 0),
        "remaining": int(row["remaining"] or 0),
        "not_caught_up_codes": list(row["not_caught_up_codes"] or []),
    }
