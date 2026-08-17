from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import TradeCalendarDay


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
