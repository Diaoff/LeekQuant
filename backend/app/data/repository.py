from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import DailyKline, StockBasic, StockFundamental, TradeCalendarDay
from app.data.stock_scope import excluded_stock_sql_condition, supported_stock_sql_condition


async def upsert_stock_basic(session: AsyncSession, records: list[StockBasic]) -> int:
    if not records:
        return 0
    values = [asdict(record) for record in records]
    await session.execute(
        text(
            """
            INSERT INTO stock_basic (
                ts_code, symbol, name, market, exchange, industry, area, list_date, delist_date,
                is_st, is_delisted, data_source, updated_at
            )
            VALUES (
                :ts_code, :symbol, :name, :market, :exchange, :industry, :area, :list_date, :delist_date,
                :is_st, :is_delisted, :data_source, NOW()
            )
            ON CONFLICT (ts_code) DO UPDATE SET
                symbol = EXCLUDED.symbol,
                name = EXCLUDED.name,
                market = EXCLUDED.market,
                exchange = EXCLUDED.exchange,
                industry = EXCLUDED.industry,
                area = EXCLUDED.area,
                list_date = EXCLUDED.list_date,
                delist_date = EXCLUDED.delist_date,
                is_st = EXCLUDED.is_st,
                is_delisted = EXCLUDED.is_delisted,
                data_source = EXCLUDED.data_source,
                updated_at = NOW()
            """
        ),
        values,
    )
    return len(records)


async def backfill_stock_basic_market(session: AsyncSession) -> int:
    result = await session.execute(
        text(
            """
            UPDATE stock_basic
            SET market = CASE
                WHEN ts_code LIKE '688%%' OR ts_code LIKE '689%%' THEN '科创板'
                WHEN ts_code LIKE '30%%' OR ts_code LIKE '301%%' THEN '创业板'
                WHEN ts_code LIKE '4%%' OR ts_code LIKE '8%%' THEN '北交所'
                ELSE '主板'
            END,
                updated_at = NOW()
            WHERE market IS NULL OR market = ''
            """
        )
    )
    return getattr(result, "rowcount", 0) or 0


async def delete_unsupported_stock_data(session: AsyncSession) -> dict[str, int]:
    supported = supported_stock_sql_condition("stock_basic")
    excluded = excluded_stock_sql_condition()
    statements = [
        (
            "sim_trades",
            "DELETE FROM sim_trades WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = sim_trades.ts_code AND "
            + supported
            + ")",
        ),
        (
            "sim_orders",
            "DELETE FROM sim_orders WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = sim_orders.ts_code AND "
            + supported
            + ")",
        ),
        (
            "sim_positions",
            "DELETE FROM sim_positions WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = sim_positions.ts_code AND "
            + supported
            + ")",
        ),
        (
            "signal_log",
            "DELETE FROM signal_log WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = signal_log.ts_code AND "
            + supported
            + ")",
        ),
        (
            "scoring_rank",
            "DELETE FROM scoring_rank WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = scoring_rank.ts_code AND "
            + supported
            + ")",
        ),
        (
            "factor_values",
            "DELETE FROM factor_values WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = factor_values.ts_code AND "
            + supported
            + ")",
        ),
        (
            "stock_fundamentals",
            "DELETE FROM stock_fundamentals WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = stock_fundamentals.ts_code AND "
            + supported
            + ")",
        ),
        (
            "watchlist",
            "DELETE FROM watchlist WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = watchlist.ts_code AND "
            + supported
            + ")",
        ),
        (
            "daily_kline",
            "DELETE FROM daily_kline WHERE NOT EXISTS (SELECT 1 FROM stock_basic WHERE stock_basic.ts_code = daily_kline.ts_code AND "
            + supported
            + ")",
        ),
        ("stock_basic", "DELETE FROM stock_basic WHERE " + excluded),
    ]
    deleted: dict[str, int] = {}
    for name, statement in statements:
        result = await session.execute(text(statement))
        deleted[name] = getattr(result, "rowcount", 0) or 0
    return deleted


async def upsert_trade_calendar(session: AsyncSession, records: list[TradeCalendarDay]) -> int:
    if not records:
        return 0
    values = [asdict(record) for record in records]
    await session.execute(
        text(
            """
            INSERT INTO trade_calendar (
                cal_date, is_open, pretrade_date, nexttrade_date, is_weekend, is_holiday, source, updated_at
            )
            VALUES (
                :cal_date, :is_open, :pretrade_date, :nexttrade_date, :is_weekend, :is_holiday, :source, NOW()
            )
            ON CONFLICT (cal_date) DO UPDATE SET
                is_open = EXCLUDED.is_open,
                pretrade_date = EXCLUDED.pretrade_date,
                nexttrade_date = EXCLUDED.nexttrade_date,
                is_weekend = EXCLUDED.is_weekend,
                is_holiday = EXCLUDED.is_holiday,
                source = EXCLUDED.source,
                updated_at = NOW()
            """
        ),
        values,
    )
    return len(records)


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
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                pre_close = EXCLUDED.pre_close,
                volume = EXCLUDED.volume,
                amount = EXCLUDED.amount,
                turnover_rate = EXCLUDED.turnover_rate,
                adj_factor = COALESCE(EXCLUDED.adj_factor, daily_kline.adj_factor),
                is_suspended = EXCLUDED.is_suspended,
                is_limit_up = EXCLUDED.is_limit_up,
                is_limit_down = EXCLUDED.is_limit_down,
                data_source = EXCLUDED.data_source,
                raw_payload = EXCLUDED.raw_payload,
                updated_at = NOW()
            """
        ),
        values,
    )
    return len(records)


async def upsert_stock_fundamentals(session: AsyncSession, records: list[StockFundamental]) -> int:
    if not records:
        return 0
    values = []
    for record in records:
        value = asdict(record)
        for key in ("income_statement", "balance_sheet", "cashflow_statement"):
            value[key] = json.dumps(value[key], ensure_ascii=False, default=str) if value[key] is not None else None
        values.append(value)
    await session.execute(
        text(
            """
            INSERT INTO stock_fundamentals (
                ts_code, report_date, announce_date, pe_ttm, pb, ps_ttm, pcf_ttm,
                roe, roa, market_cap, float_market_cap, dividend_yield, revenue,
                net_profit, revenue_growth, net_profit_growth, gross_margin,
                debt_to_equity, current_ratio, free_cash_flow, income_statement,
                balance_sheet, cashflow_statement, data_source, updated_at
            )
            VALUES (
                :ts_code, :report_date, :announce_date, :pe_ttm, :pb, :ps_ttm, :pcf_ttm,
                :roe, :roa, :market_cap, :float_market_cap, :dividend_yield, :revenue,
                :net_profit, :revenue_growth, :net_profit_growth, :gross_margin,
                :debt_to_equity, :current_ratio, :free_cash_flow,
                CAST(:income_statement AS JSONB), CAST(:balance_sheet AS JSONB),
                CAST(:cashflow_statement AS JSONB), :data_source, NOW()
            )
            ON CONFLICT (ts_code, report_date) DO UPDATE SET
                announce_date = COALESCE(EXCLUDED.announce_date, stock_fundamentals.announce_date),
                pe_ttm = COALESCE(EXCLUDED.pe_ttm, stock_fundamentals.pe_ttm),
                pb = COALESCE(EXCLUDED.pb, stock_fundamentals.pb),
                ps_ttm = COALESCE(EXCLUDED.ps_ttm, stock_fundamentals.ps_ttm),
                pcf_ttm = COALESCE(EXCLUDED.pcf_ttm, stock_fundamentals.pcf_ttm),
                roe = COALESCE(EXCLUDED.roe, stock_fundamentals.roe),
                roa = COALESCE(EXCLUDED.roa, stock_fundamentals.roa),
                market_cap = COALESCE(EXCLUDED.market_cap, stock_fundamentals.market_cap),
                float_market_cap = COALESCE(EXCLUDED.float_market_cap, stock_fundamentals.float_market_cap),
                dividend_yield = COALESCE(EXCLUDED.dividend_yield, stock_fundamentals.dividend_yield),
                revenue = COALESCE(EXCLUDED.revenue, stock_fundamentals.revenue),
                net_profit = COALESCE(EXCLUDED.net_profit, stock_fundamentals.net_profit),
                revenue_growth = COALESCE(EXCLUDED.revenue_growth, stock_fundamentals.revenue_growth),
                net_profit_growth = COALESCE(EXCLUDED.net_profit_growth, stock_fundamentals.net_profit_growth),
                gross_margin = COALESCE(EXCLUDED.gross_margin, stock_fundamentals.gross_margin),
                debt_to_equity = COALESCE(EXCLUDED.debt_to_equity, stock_fundamentals.debt_to_equity),
                current_ratio = COALESCE(EXCLUDED.current_ratio, stock_fundamentals.current_ratio),
                free_cash_flow = COALESCE(EXCLUDED.free_cash_flow, stock_fundamentals.free_cash_flow),
                income_statement = COALESCE(EXCLUDED.income_statement, stock_fundamentals.income_statement),
                balance_sheet = COALESCE(EXCLUDED.balance_sheet, stock_fundamentals.balance_sheet),
                cashflow_statement = COALESCE(EXCLUDED.cashflow_statement, stock_fundamentals.cashflow_statement),
                data_source = EXCLUDED.data_source,
                updated_at = NOW()
            """
        ),
        values,
    )
    return len(records)


async def record_update_success(
    session: AsyncSession,
    data_type: str,
    source: str,
    *,
    ts_code: str | None = None,
    last_trade_date: date | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO data_update_state (
                data_type, ts_code, source, last_trade_date, last_success_at, failure_count, error_message, updated_at
            )
            VALUES (:data_type, :ts_code, :source, :last_trade_date, NOW(), 0, NULL, NOW())
            ON CONFLICT (data_type, ts_code, source) DO UPDATE SET
                last_trade_date = COALESCE(EXCLUDED.last_trade_date, data_update_state.last_trade_date),
                last_success_at = NOW(),
                failure_count = 0,
                error_message = NULL,
                updated_at = NOW()
            """
        ),
        {
            "data_type": data_type,
            "ts_code": ts_code,
            "source": source,
            "last_trade_date": last_trade_date,
        },
    )


async def record_update_failure(
    session: AsyncSession,
    data_type: str,
    source: str,
    error_message: str,
    *,
    ts_code: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO data_update_state (
                data_type, ts_code, source, last_failure_at, failure_count, error_message, updated_at
            )
            VALUES (:data_type, :ts_code, :source, NOW(), 1, :error_message, NOW())
            ON CONFLICT (data_type, ts_code, source) DO UPDATE SET
                last_failure_at = NOW(),
                failure_count = data_update_state.failure_count + 1,
                error_message = EXCLUDED.error_message,
                updated_at = NOW()
            """
        ),
        {
            "data_type": data_type,
            "ts_code": ts_code,
            "source": source,
            "error_message": error_message[:4000],
        },
    )


async def create_alert(
    session: AsyncSession,
    *,
    level: str,
    category: str,
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO alert_events (level, category, title, message, payload)
            VALUES (:level, :category, :title, :message, CAST(:payload AS JSONB))
            """
        ),
        {
            "level": level,
            "category": category,
            "title": title,
            "message": message,
            "payload": json.dumps(payload or {}, ensure_ascii=False, default=str),
        },
    )


async def list_alerts(
    session: AsyncSession,
    *,
    level: str | None = None,
    category: str | None = None,
    is_resolved: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    filters = []
    params: dict[str, Any] = {
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }
    if level is not None:
        filters.append("level = :level")
        params["level"] = level
    if category is not None:
        filters.append("category = :category")
        params["category"] = category
    if is_resolved is not None:
        filters.append("is_resolved = :is_resolved")
        params["is_resolved"] = is_resolved

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    result = await session.execute(
        text(
            f"""
            SELECT id, level, category, title, message, payload, is_resolved, created_at, resolved_at
            FROM alert_events
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


async def create_pending_task_run(
    session: AsyncSession,
    *,
    task_name: str,
    task_id: str,
    payload: dict[str, Any] | None = None,
) -> int:
    result = await session.execute(
        text(
            """
            INSERT INTO task_runs (task_name, task_id, status, payload)
            VALUES (:task_name, :task_id, 'pending', CAST(:payload AS JSONB))
            RETURNING id
            """
        ),
        {
            "task_name": task_name,
            "task_id": task_id,
            "payload": json.dumps(payload or {}, ensure_ascii=False, default=str),
        },
    )
    await session.commit()
    return int(result.scalar_one())


async def get_active_task_run(session: AsyncSession, *, task_names: list[str]) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT id, task_name, task_id, status, started_at, payload
            FROM task_runs
            WHERE task_name = ANY(:task_names)
              AND status IN ('pending', 'running')
            ORDER BY started_at DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"task_names": task_names},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def mark_stale_running_task_runs(
    session: AsyncSession,
    *,
    older_than: timedelta = timedelta(hours=24),
    task_names: list[str] | None = None,
    error_message: str = "stale running task after celery worker cleanup",
) -> int:
    task_name_filter = ""
    cutoff_at = datetime.now(tz=UTC) - older_than
    params: dict[str, Any] = {
        "cutoff_at": cutoff_at,
        "error_message": error_message[:4000],
    }
    if task_names:
        task_name_filter = "AND task_name = ANY(CAST(:task_names AS TEXT[]))"
        params["task_names"] = task_names

    result = await session.execute(
        text(
            f"""
            UPDATE task_runs
            SET status = 'failed',
                finished_at = NOW(),
                error_message = COALESCE(error_message, :error_message)
            WHERE status = 'running'
              AND started_at < :cutoff_at
              {task_name_filter}
            RETURNING id
            """
        ),
        params,
    )
    rows = result.fetchall()
    await session.commit()
    return len(rows)


async def mark_task_run_queue_failed(
    session: AsyncSession,
    *,
    task_id: str,
    error_message: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE task_runs
            SET status = 'failed',
                finished_at = NOW(),
                error_message = :error_message
            WHERE task_id = :task_id
            """
        ),
        {
            "task_id": task_id,
            "error_message": error_message[:4000],
        },
    )
    await session.commit()


async def mark_task_run_failed(
    session: AsyncSession,
    *,
    task_id: str,
    error_message: str,
    statuses: list[str] | None = None,
) -> None:
    status_filter = ""
    params: dict[str, Any] = {
        "task_id": task_id,
        "error_message": error_message[:4000],
    }
    if statuses:
        status_filter = "AND status = ANY(CAST(:statuses AS TEXT[]))"
        params["statuses"] = statuses

    await session.execute(
        text(
            f"""
            UPDATE task_runs
            SET status = 'failed',
                finished_at = NOW(),
                error_message = COALESCE(error_message, :error_message)
            WHERE task_id = :task_id
              {status_filter}
            """
        ),
        params,
    )
    await session.commit()
