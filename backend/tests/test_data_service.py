from datetime import date

import pytest

from app.data.models import DailyKline, StockBasic
from app.data.service import sync_kline, sync_stock_basic

pytestmark = pytest.mark.asyncio


class FakeProvider:
    name = "fake"

    def fetch_stock_basic(self):
        return [StockBasic(ts_code="000001.SZ", symbol="000001", name="平安银行")]

    def fetch_trade_calendar(self, start_date, end_date):
        return []

    def fetch_daily_kline(self, ts_code, start_date, end_date):
        return [
            DailyKline(
                ts_code=ts_code,
                trade_date=start_date,
                open=10,
                high=11,
                low=9,
                close=10.5,
                data_source=self.name,
            )
        ]


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self):
        self.select_calls = 0
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT ts_code" in sql:
            self.select_calls += 1
            if self.select_calls == 1:
                return FakeResult([])
            return FakeResult([("000001.SZ",)])
        return FakeResult([])

    async def commit(self):
        self.commits += 1


async def test_sync_kline_bootstraps_stock_basic_when_sample_is_empty(monkeypatch) -> None:
    import app.data.service as service

    calls = {"stock": 0, "kline": 0}

    async def fake_upsert_stock_basic(_session, records):
        calls["stock"] += len(records)
        return len(records)

    async def fake_upsert_daily_kline(_session, records):
        calls["kline"] += len(records)
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "upsert_stock_basic", fake_upsert_stock_basic)
    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)

    result = await sync_kline(
        FakeSession(),
        None,
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[FakeProvider()],
    )

    assert calls == {"stock": 1, "kline": 1}
    assert result["requested_symbols"] == 1
    assert result["inserted_or_updated"] == 1


async def test_sync_stock_basic_skips_invalid_rows(monkeypatch) -> None:
    import app.data.service as service

    class MixedProvider:
        name = "mixed"

        def fetch_stock_basic(self):
            return [
                StockBasic(ts_code="000001.SZ", symbol="000001", name="平安银行"),
                StockBasic(ts_code="000002.SZ", symbol="000002", name=""),
            ]

    calls = {"stock": 0, "alerts": 0, "success": 0}

    async def fake_upsert_stock_basic(_session, records):
        calls["stock"] += len(records)
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        calls["success"] += 1

    async def fake_create_alert(*_args, **_kwargs):
        calls["alerts"] += 1

    monkeypatch.setattr(service, "upsert_stock_basic", fake_upsert_stock_basic)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)
    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    result = await sync_stock_basic(FakeSession(), providers=[MixedProvider()])

    assert result == {"source": "mixed", "inserted_or_updated": 1, "skipped": 1}
    assert calls == {"stock": 1, "alerts": 1, "success": 1}


async def test_sync_stock_basic_fails_when_all_rows_are_invalid(monkeypatch) -> None:
    import app.data.service as service

    class BadProvider:
        name = "bad"

        def fetch_stock_basic(self):
            return [
                StockBasic(ts_code="000001.SZ", symbol="000001", name=""),
                StockBasic(ts_code="000002.SZ", symbol="000002", name=""),
            ]

    calls = {"failure": 0, "alerts": 0}

    async def fake_record_update_failure(*_args, **_kwargs):
        calls["failure"] += 1

    async def fake_create_alert(*_args, **_kwargs):
        calls["alerts"] += 1

    monkeypatch.setattr(service, "record_update_failure", fake_record_update_failure)
    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    with pytest.raises(ValueError, match="no valid records"):
        await sync_stock_basic(FakeSession(), providers=[BadProvider()])

    assert calls == {"failure": 1, "alerts": 1}
