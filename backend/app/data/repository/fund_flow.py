from __future__ import annotations

from dataclasses import asdict
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import FundFlowDaily


async def upsert_fund_flow(session: AsyncSession, records: list[FundFlowDaily]) -> int:
    if not records:
        return 0
    values = [asdict(r) for r in records]
    await session.execute(
        text(
            """
            INSERT INTO fund_flow_daily (
                ts_code, trade_date,
                main_net_amount, main_net_ratio,
                ultra_net_amount, ultra_net_ratio,
                large_net_amount, large_net_ratio,
                mid_net_amount, mid_net_ratio,
                small_net_amount, small_net_ratio,
                data_source, updated_at
            )
            VALUES (
                :ts_code, :trade_date,
                :main_net_amount, :main_net_ratio,
                :ultra_net_amount, :ultra_net_ratio,
                :large_net_amount, :large_net_ratio,
                :mid_net_amount, :mid_net_ratio,
                :small_net_amount, :small_net_ratio,
                :data_source, NOW()
            )
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                main_net_amount   = COALESCE(EXCLUDED.main_net_amount,   fund_flow_daily.main_net_amount),
                main_net_ratio    = COALESCE(EXCLUDED.main_net_ratio,    fund_flow_daily.main_net_ratio),
                ultra_net_amount  = COALESCE(EXCLUDED.ultra_net_amount,  fund_flow_daily.ultra_net_amount),
                ultra_net_ratio   = COALESCE(EXCLUDED.ultra_net_ratio,   fund_flow_daily.ultra_net_ratio),
                large_net_amount  = COALESCE(EXCLUDED.large_net_amount,  fund_flow_daily.large_net_amount),
                large_net_ratio   = COALESCE(EXCLUDED.large_net_ratio,   fund_flow_daily.large_net_ratio),
                mid_net_amount    = COALESCE(EXCLUDED.mid_net_amount,    fund_flow_daily.mid_net_amount),
                mid_net_ratio     = COALESCE(EXCLUDED.mid_net_ratio,     fund_flow_daily.mid_net_ratio),
                small_net_amount  = COALESCE(EXCLUDED.small_net_amount,  fund_flow_daily.small_net_amount),
                small_net_ratio   = COALESCE(EXCLUDED.small_net_ratio,   fund_flow_daily.small_net_ratio),
                data_source       = EXCLUDED.data_source,
                updated_at        = NOW()
            """
        ),
        values,
    )
    return len(records)


async def get_recent_fund_flow(
    session: AsyncSession, ts_code: str, days: int = 30
) -> list[FundFlowDaily]:
    result = await session.execute(
        text(
            """
            SELECT ts_code, trade_date,
                   main_net_amount, main_net_ratio,
                   ultra_net_amount, ultra_net_ratio,
                   large_net_amount, large_net_ratio,
                   mid_net_amount, mid_net_ratio,
                   small_net_amount, small_net_ratio,
                   data_source
            FROM fund_flow_daily
            WHERE ts_code = :ts_code
            ORDER BY trade_date DESC
            LIMIT :days
            """
        ),
        {"ts_code": ts_code, "days": days},
    )
    rows = result.mappings().all()
    out: list[FundFlowDaily] = []
    for row in rows:
        out.append(FundFlowDaily(
            ts_code=row["ts_code"],
            trade_date=row["trade_date"],
            main_net_amount=row["main_net_amount"],
            main_net_ratio=row["main_net_ratio"],
            ultra_net_amount=row["ultra_net_amount"],
            ultra_net_ratio=row["ultra_net_ratio"],
            large_net_amount=row["large_net_amount"],
            large_net_ratio=row["large_net_ratio"],
            mid_net_amount=row["mid_net_amount"],
            mid_net_ratio=row["mid_net_ratio"],
            small_net_amount=row["small_net_amount"],
            small_net_ratio=row["small_net_ratio"],
            data_source=row["data_source"] or "akshare",
        ))
    # Return in ascending date order
    return list(reversed(out))
