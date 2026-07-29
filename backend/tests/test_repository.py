from datetime import date
from decimal import Decimal
import json

import pytest

from app.data.models import DailyKline, StockBasic
from app.data.repository import (
    backfill_stock_basic_market,
    get_sync_progress,
    upsert_daily_kline,
    upsert_stock_basic,
)

pytestmark = pytest.mark.asyncio


class CaptureSession:
    def __init__(self):
        self.statement = None
        self.params = None

    async def execute(self, statement, params=None):
        self.statement = statement
        self.params = params


class ResultCaptureSession:
    """Fake session that returns a fixed result row for ``get_sync_progress``.

    ``get_sync_progress`` runs a single ``session.execute(text(...), params)``
    and reads ``result.mappings().one()``. This captures the SQL + bound params
    (so we can assert the CTEs and ``:has_ts_codes``/``:has_watchlist`` flags)
    while returning a canned row, so no Postgres is needed.
    """

    def __init__(self, row):
        self.statement = None
        self.params = None
        self._row = row

    async def execute(self, statement, params=None):
        self.statement = statement
        self.params = params
        return self

    def mappings(self):
        return self

    def one(self):
        return self._row


async def test_upsert_stock_basic_does_not_require_raw_payload() -> None:
    session = CaptureSession()

    count = await upsert_stock_basic(
        session,
        [StockBasic(ts_code="000001.SZ", symbol="000001", name="平安银行")],
    )

    assert count == 1
    assert session.params[0]["ts_code"] == "000001.SZ"
    assert "raw_payload" not in session.params[0]


async def test_backfill_stock_basic_market_updates_empty_values() -> None:
    session = CaptureSession()

    count = await backfill_stock_basic_market(session)

    assert count == 0
    assert "UPDATE stock_basic" in str(session.statement)
    assert "WHERE market IS NULL OR market = ''" in str(session.statement)


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


async def test_get_sync_progress_builds_scope_query_for_ts_codes() -> None:
    row = {
        "latest_open_day": date(2026, 5, 31),
        "total": 1,
        "caught_up": 1,
        "remaining": 0,
        "failed": 0,
        "not_caught_up_codes": [],
    }
    session = ResultCaptureSession(row)

    progress = await get_sync_progress(session, ts_codes=["000001.SZ"])

    sql = str(session.statement)
    # The four CTEs that define the progress snapshot must all be present.
    assert "latest" in sql and "scope" in sql and "dk" in sql and "dus" in sql
    # ts_codes scope: flag on, codes bound, watchlist flag off.
    assert session.params["has_ts_codes"] is True
    assert session.params["ts_codes"] == ["000001.SZ"]
    assert session.params["has_watchlist"] is False
    # Returned shape is the contract the API serializes.
    assert progress == {
        "latest_open_day": date(2026, 5, 31),
        "total": 1,
        "caught_up": 1,
        "remaining": 0,
        "failed": 0,
        "not_caught_up_codes": [],
    }


async def test_get_sync_progress_builds_scope_query_for_watchlist() -> None:
    row = {
        "latest_open_day": date(2026, 5, 31),
        "total": 3,
        "caught_up": 2,
        "remaining": 1,
        "failed": 0,
        "not_caught_up_codes": ["600000.SH"],
    }
    session = ResultCaptureSession(row)

    progress = await get_sync_progress(session, watchlist_id=1)

    sql = str(session.statement)
    assert "watchlist_groups" in sql and "watchlist" in sql
    # watchlist scope: flag on, ts_codes flag off.
    assert session.params["has_watchlist"] is True
    assert session.params["has_ts_codes"] is False
    assert progress["total"] == 3
    assert progress["remaining"] == 1
    assert progress["not_caught_up_codes"] == ["600000.SH"]
