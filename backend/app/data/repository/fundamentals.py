from __future__ import annotations

from dataclasses import asdict
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import StockFundamental


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
