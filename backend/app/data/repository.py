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


async def resolve_alert(session: AsyncSession, alert_id: int) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            UPDATE alert_events
            SET is_resolved = TRUE,
                resolved_at = NOW()
            WHERE id = :alert_id
            RETURNING id, level, category, title, message, payload, is_resolved, created_at, resolved_at
            """
        ),
        {"alert_id": alert_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    await session.commit()
    return dict(row)


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


async def mark_task_run_cancelled(
    session: AsyncSession,
    *,
    task_id: str,
    error_message: str,
) -> None:
    """Mark a task_runs row as cancelled (e.g. beat lock skipped).

    Only touches non-terminal rows so a status the task body already wrote is
    never overwritten.
    """
    await session.execute(
        text(
            """
            UPDATE task_runs
            SET status = 'cancelled',
                finished_at = NOW(),
                error_message = COALESCE(error_message, :error_message)
            WHERE task_id = :task_id
              AND status IN ('pending', 'running')
            """
        ),
        {
            "task_id": task_id,
            "error_message": error_message[:4000],
        },
    )
    await session.commit()


async def reconcile_task_run_status(
    session: AsyncSession,
    *,
    task_id: str,
    status: str,
    error_message: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """Backstop reconciliation of a task_runs row to Celery's terminal state.

    Called from the Celery ``task_failure`` / ``task_success`` / ``task_revoked``
    signals. Idempotent: only updates rows still in ``status IN ('pending', 'running')``,
    so it never overwrites a status the task body already wrote.
    """
    await session.execute(
        text(
            """
            UPDATE task_runs
            SET status = :status,
                finished_at = COALESCE(finished_at, NOW()),
                error_message = COALESCE(error_message, :error_message),
                result = COALESCE(CAST(:result AS JSONB), result)
            WHERE task_id = :task_id
              AND status IN ('pending', 'running')
            """
        ),
        {
            "task_id": task_id,
            "status": status,
            "error_message": error_message[:4000] if error_message else None,
            "result": json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
        },
    )
    await session.commit()


async def get_latest_task_run(
    session: AsyncSession,
    *,
    task_name: str,
) -> dict[str, Any] | None:
    """Return the most recent task_runs row for ``task_name`` (any status)."""
    result = await session.execute(
        text(
            """
            SELECT id, task_name, task_id, status, started_at, finished_at,
                   duration_ms, payload, result, error_message
            FROM task_runs
            WHERE task_name = :task_name
            ORDER BY started_at DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"task_name": task_name},
    )
    row = result.mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# K-line sync DB queue (kline_sync_jobs / kline_sync_items)
# ---------------------------------------------------------------------------


async def create_kline_sync_job(
    session: AsyncSession,
    *,
    job_type: str,
    config: dict[str, Any] | None = None,
) -> int:
    """Insert a kline_sync_jobs row (status='running') and return its id."""
    result = await session.execute(
        text(
            """
            INSERT INTO kline_sync_jobs (job_type, status, config, started_at)
            VALUES (:job_type, 'running', CAST(:config AS JSONB), NOW())
            RETURNING id
            """
        ),
        {
            "job_type": job_type,
            "config": json.dumps(config or {}, ensure_ascii=False, default=str),
        },
    )
    job_id = int(result.scalar_one())
    await session.commit()
    return job_id


async def insert_kline_sync_items(
    session: AsyncSession,
    *,
    job_id: int,
    items: list[dict[str, Any]],
) -> int:
    """Bulk-insert work items for a job and bump the job's scope_total."""
    if not items:
        await session.commit()
        return 0
    values = [
        {
            "job_id": job_id,
            "ts_code": item["ts_code"],
            "start_date": item["start_date"],
            "end_date": item["end_date"],
        }
        for item in items
    ]
    await session.execute(
        text(
            """
            INSERT INTO kline_sync_items (job_id, ts_code, start_date, end_date, status)
            VALUES (:job_id, :ts_code, :start_date, :end_date, 'pending')
            ON CONFLICT (job_id, ts_code, start_date, end_date) DO NOTHING
            """
        ),
        values,
    )
    await session.execute(
        text(
            """
            UPDATE kline_sync_jobs
            SET scope_total = (SELECT COUNT(*) FROM kline_sync_items WHERE job_id = :job_id)
            WHERE id = :job_id
            """
        ),
        {"job_id": job_id},
    )
    await session.commit()
    return len(values)


async def claim_kline_sync_items(
    session: AsyncSession,
    *,
    job_id: int,
    count: int,
    worker_id: str,
) -> list[dict[str, Any]]:
    """Atomically claim up to ``count`` pending items for a worker.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers never claim the same
    item. Claiming increments ``attempts`` (a claim IS an attempt) and stamps
    ``last_attempt_at`` for stuck-item detection.
    """
    result = await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = 'running',
                worker_id = :worker_id,
                attempts = attempts + 1,
                last_attempt_at = NOW()
            WHERE id IN (
                SELECT id
                FROM kline_sync_items
                WHERE job_id = :job_id AND status = 'pending'
                ORDER BY id
                LIMIT :count
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, ts_code, start_date, end_date, attempts
            """
        ),
        {"job_id": job_id, "count": max(1, count), "worker_id": worker_id},
    )
    rows = [dict(row) for row in result.mappings().all()]
    await session.commit()
    return rows


async def mark_item_done(
    session: AsyncSession,
    *,
    item_id: int,
    job_id: int,
) -> None:
    """Mark an item done and bump the job's scope_done counter."""
    await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = 'done', worker_id = NULL, last_error = NULL
            WHERE id = :item_id
            """
        ),
        {"item_id": item_id},
    )
    await session.execute(
        text("UPDATE kline_sync_jobs SET scope_done = scope_done + 1 WHERE id = :job_id"),
        {"job_id": job_id},
    )
    await session.commit()


async def mark_item_failed(
    session: AsyncSession,
    *,
    item_id: int,
    job_id: int,
    error: str,
    max_attempts: int,
) -> bool:
    """Record a failure for an item.

    ``attempts`` is NOT incremented here — ``claim_kline_sync_items`` already
    counted this attempt; incrementing again would double-count each failure.
    Returns True when the item crossed ``max_attempts`` and became
    ``permanently_failed`` (job counters updated accordingly).
    """
    result = await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = CASE
                    WHEN attempts >= :max_attempts THEN 'permanently_failed'
                    ELSE 'pending'
                END,
                worker_id = NULL,
                last_error = :error
            WHERE id = :item_id
            RETURNING status, ts_code
            """
        ),
        {"item_id": item_id, "max_attempts": max_attempts, "error": error[:4000]},
    )
    row = result.mappings().one_or_none()
    is_permanent = bool(row and row["status"] == "permanently_failed")
    if is_permanent:
        await session.execute(
            text(
                """
                UPDATE kline_sync_jobs
                SET scope_failed = scope_failed + 1,
                    permanent_failure_codes = array_append(
                        COALESCE(permanent_failure_codes, ARRAY[]::TEXT[]), :ts_code
                    )
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id, "ts_code": row["ts_code"]},
        )
    await session.commit()
    return is_permanent


async def recover_stuck_items(
    session: AsyncSession,
    *,
    stuck_seconds: int,
) -> int:
    """Reset 'running' items whose last attempt is older than ``stuck_seconds``."""
    result = await session.execute(
        text(
            """
            UPDATE kline_sync_items
            SET status = 'pending', worker_id = NULL
            WHERE status = 'running'
              AND (
                  last_attempt_at IS NULL
                  OR last_attempt_at < NOW() - make_interval(secs => :stuck_seconds)
              )
            RETURNING id
            """
        ),
        {"stuck_seconds": stuck_seconds},
    )
    rows = result.fetchall()
    await session.commit()
    return len(rows)


async def complete_job_if_done(
    session: AsyncSession,
    *,
    job_id: int,
) -> bool:
    """Mark the job completed when no pending/running items remain."""
    result = await session.execute(
        text(
            """
            UPDATE kline_sync_jobs
            SET status = 'completed', completed_at = NOW()
            WHERE id = :job_id
              AND status = 'running'
              AND NOT EXISTS (
                  SELECT 1 FROM kline_sync_items
                  WHERE job_id = :job_id AND status IN ('pending', 'running')
              )
            RETURNING id
            """
        ),
        {"job_id": job_id},
    )
    completed = result.first() is not None
    await session.commit()
    return completed


async def get_job_progress(
    session: AsyncSession,
    *,
    job_id: int,
) -> dict[str, Any] | None:
    """Return a job row merged with live per-status item counts."""
    result = await session.execute(
        text(
            """
            SELECT
                j.id, j.job_type, j.status,
                j.scope_total, j.scope_done, j.scope_failed,
                j.permanent_failure_codes, j.config,
                j.created_at, j.started_at, j.completed_at, j.error,
                COUNT(i.id)::INT AS item_total,
                COUNT(*) FILTER (WHERE i.status = 'pending')::INT AS pending,
                COUNT(*) FILTER (WHERE i.status = 'running')::INT AS running,
                COUNT(*) FILTER (WHERE i.status = 'done')::INT AS done,
                COUNT(*) FILTER (WHERE i.status = 'permanently_failed')::INT AS permanently_failed
            FROM kline_sync_jobs j
            LEFT JOIN kline_sync_items i ON i.job_id = j.id
            WHERE j.id = :job_id
            GROUP BY j.id
            """
        ),
        {"job_id": job_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    progress = dict(row)
    progress["permanent_failure_codes"] = list(progress.get("permanent_failure_codes") or [])
    return progress


async def list_job_items(
    session: AsyncSession,
    *,
    job_id: int,
    status: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List items for a job, optionally filtered by status."""
    status_filter = ""
    params: dict[str, Any] = {"job_id": job_id, "limit": max(1, min(limit, 1000))}
    if status is not None:
        status_filter = "AND status = :status"
        params["status"] = status
    result = await session.execute(
        text(
            f"""
            SELECT id, ts_code, start_date, end_date, status, attempts,
                   last_error, last_attempt_at, worker_id
            FROM kline_sync_items
            WHERE job_id = :job_id
              {status_filter}
            ORDER BY id
            LIMIT :limit
            """
        ),
        params,
    )
    items = [dict(row) for row in result.mappings().all()]
    return {"job_id": job_id, "items": items, "count": len(items)}


async def list_recent_jobs(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List recent jobs (newest first) with live per-status item counts."""
    result = await session.execute(
        text(
            """
            SELECT
                j.id, j.job_type, j.status,
                j.scope_total, j.scope_done, j.scope_failed,
                j.permanent_failure_codes, j.config,
                j.created_at, j.started_at, j.completed_at, j.error,
                COUNT(i.id)::INT AS item_total,
                COUNT(*) FILTER (WHERE i.status = 'pending')::INT AS pending,
                COUNT(*) FILTER (WHERE i.status = 'running')::INT AS running,
                COUNT(*) FILTER (WHERE i.status = 'done')::INT AS done,
                COUNT(*) FILTER (WHERE i.status = 'permanently_failed')::INT AS permanently_failed
            FROM kline_sync_jobs j
            LEFT JOIN kline_sync_items i ON i.job_id = j.id
            GROUP BY j.id
            ORDER BY j.created_at DESC, j.id DESC
            LIMIT :limit
            """
        ),
        {"limit": max(1, min(limit, 100))},
    )
    jobs = []
    for row in result.mappings().all():
        job = dict(row)
        job["permanent_failure_codes"] = list(job.get("permanent_failure_codes") or [])
        jobs.append(job)
    return jobs


# ---------------------------------------------------------------------------
# Sync progress (source of truth: daily_kline vs latest open trading day)
# ---------------------------------------------------------------------------


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
