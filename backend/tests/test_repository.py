from datetime import date
from decimal import Decimal
import json

import pytest

from app.data.models import DailyKline, StockBasic
from app.data.repository import upsert_daily_kline, upsert_stock_basic

pytestmark = pytest.mark.asyncio


class CaptureSession:
    def __init__(self):
        self.statement = None
        self.params = None

    async def execute(self, statement, params=None):
        self.statement = statement
        self.params = params


async def test_upsert_stock_basic_does_not_require_raw_payload() -> None:
    session = CaptureSession()

    count = await upsert_stock_basic(
        session,
        [StockBasic(ts_code="000001.SZ", symbol="000001", name="平安银行")],
    )

    assert count == 1
    assert session.params[0]["ts_code"] == "000001.SZ"
    assert "raw_payload" not in session.params[0]


async def test_upsert_daily_kline_serializes_raw_payload() -> None:
    session = CaptureSession()

    count = await upsert_daily_kline(
        session,
        [
            DailyKline(
                ts_code="000001.SZ",
                trade_date=date(2026, 5, 18),
                open=Decimal("10"),
                high=Decimal("11"),
                low=Decimal("9"),
                close=Decimal("10.5"),
                raw_payload={"日期": "2026-05-18"},
            )
        ],
    )

    assert count == 1
    assert json.loads(session.params[0]["raw_payload"]) == {"日期": "2026-05-18"}


async def test_upsert_daily_kline_preserves_existing_adj_factor_when_incoming_is_null() -> None:
    session = CaptureSession()

    await upsert_daily_kline(
        session,
        [
            DailyKline(
                ts_code="000001.SZ",
                trade_date=date(2026, 5, 18),
                open=Decimal("10"),
                high=Decimal("11"),
                low=Decimal("9"),
                close=Decimal("10.5"),
                adj_factor=None,
            )
        ],
    )

    assert "adj_factor = COALESCE(EXCLUDED.adj_factor, daily_kline.adj_factor)" in str(session.statement)
