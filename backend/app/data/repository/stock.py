from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import StockBasic
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
