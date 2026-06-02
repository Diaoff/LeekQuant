from datetime import date
from decimal import Decimal
import asyncio

import pytest

from app.data.models import DailyKline, StockBasic
from app.data.service import infer_incremental_kline_ranges, select_sample_stock_codes, sync_kline, sync_stock_basic

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

    def mappings(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0][0] if self._rows else None


class FakeSession:
    def __init__(self):
        self.select_calls = 0
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT is_st FROM stock_basic" in sql:
            return FakeResult([(False,)])
        if "SELECT ts_code" in sql:
            self.select_calls += 1
            if self.select_calls == 1:
                return FakeResult([])
            return FakeResult([("000001.SZ",)])
        return FakeResult([])

    async def commit(self):
        self.commits += 1


class StAwareSession(FakeSession):
    def __init__(self, *, is_st=False):
        super().__init__()
        self.is_st = is_st

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT is_st FROM stock_basic" in sql:
            return FakeResult([(self.is_st,)])
        return await super().execute(statement, params)


class StaticStockSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, statement, params=None):
        return FakeResult(self.rows)


class IncrementalRangeSession:
    def __init__(self, rows, latest_open=date(2026, 5, 29)):
        self.rows = rows
        self.latest_open = latest_open
        self.params = []
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        sql = str(statement)
        if "SELECT MAX(cal_date)" in sql:
            return FakeResult([(self.latest_open,)])
        return FakeResult(self.rows)


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


async def test_sync_kline_reports_progress_on_completion(monkeypatch) -> None:
    import app.data.service as service

    class TwoCodeSession(FakeSession):
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT ts_code" in sql:
                return FakeResult([("000001.SZ",), ("600000.SH",)])
            return FakeResult([])

    class FakeSessionContext:
        async def __aenter__(self):
            return TwoCodeSession()

        async def __aexit__(self, *_args):
            return None

    calls = []

    async def fake_upsert_daily_kline(_session, records):
        await asyncio.sleep(0)
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)

    progress = []
    result = await sync_kline(
        TwoCodeSession(),
        None,
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[FakeProvider()],
        progress_callback=lambda current, total, code: progress.append((current, total, code)),
        commit_each=True,
        concurrency=2,
        session_factory=FakeSessionContext,
    )

    assert result["requested_symbols"] == 2
    assert progress == [(1, 2, "000001.SZ"), (2, 2, "600000.SH")]


async def test_sync_kline_keeps_serial_transaction_when_commit_each_is_false(monkeypatch) -> None:
    import app.data.service as service

    class TwoCodeSession(FakeSession):
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT ts_code" in sql:
                return FakeResult([("000001.SZ",), ("600000.SH",)])
            return FakeResult([])

    calls = {"commit": 0}

    async def fake_upsert_daily_kline(_session, records):
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)

    session = TwoCodeSession()
    result = await sync_kline(
        session,
        None,
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[FakeProvider()],
        commit_each=False,
        concurrency=4,
    )

    assert result["requested_symbols"] == 2
    assert session.commits == 1


async def test_sync_kline_writes_quality_alert_for_missing_adj_factor(monkeypatch) -> None:
    import app.data.service as service

    class MissingAdjProvider:
        name = "quality"

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            return [
                DailyKline(
                    ts_code=ts_code,
                    trade_date=start_date,
                    open=Decimal("10"),
                    high=Decimal("10.5"),
                    low=Decimal("9.8"),
                    close=Decimal("10.2"),
                    pre_close=Decimal("10"),
                    adj_factor=None,
                    data_source=self.name,
                )
            ]

    captured = {}

    async def fake_upsert_daily_kline(_session, records):
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    async def fake_create_alert(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)
    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    result = await sync_kline(
        StAwareSession(),
        ["000001.SZ"],
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[MissingAdjProvider()],
    )

    assert result["inserted_or_updated"] == 1
    assert captured["category"] == "data_quality"
    assert captured["payload"]["counts"] == {"missing_adj_factor": 1}
    assert captured["payload"]["issues"][0]["ts_code"] == "000001.SZ"


async def test_sync_kline_writes_quality_alert_for_abnormal_price_change(monkeypatch) -> None:
    import app.data.service as service

    class AbnormalChangeProvider:
        name = "quality"

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            return [
                DailyKline(
                    ts_code=ts_code,
                    trade_date=start_date,
                    open=Decimal("10"),
                    high=Decimal("11.5"),
                    low=Decimal("9.8"),
                    close=Decimal("11.2"),
                    pre_close=Decimal("10"),
                    adj_factor=Decimal("1"),
                    data_source=self.name,
                )
            ]

    alerts = []

    async def fake_upsert_daily_kline(_session, records):
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    async def fake_create_alert(*_args, **kwargs):
        alerts.append(kwargs)

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)
    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    await sync_kline(
        StAwareSession(),
        ["000001.SZ"],
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[AbnormalChangeProvider()],
    )

    assert alerts[0]["payload"]["counts"] == {"abnormal_price_change": 1}
    issue = alerts[0]["payload"]["issues"][0]
    assert issue["change_pct"] == Decimal("0.12")
    assert issue["limit_pct"] == Decimal("0.10")


async def test_sync_kline_skips_quality_alert_for_suspended_or_missing_pre_close(monkeypatch) -> None:
    import app.data.service as service

    class SkippedQualityProvider:
        name = "quality"

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            return [
                DailyKline(
                    ts_code=ts_code,
                    trade_date=start_date,
                    open=Decimal("10"),
                    high=Decimal("11.5"),
                    low=Decimal("9.8"),
                    close=Decimal("11.2"),
                    pre_close=Decimal("10"),
                    adj_factor=None,
                    is_suspended=True,
                    data_source=self.name,
                ),
                DailyKline(
                    ts_code=ts_code,
                    trade_date=end_date,
                    open=Decimal("10"),
                    high=Decimal("11.5"),
                    low=Decimal("9.8"),
                    close=Decimal("11.2"),
                    pre_close=None,
                    adj_factor=Decimal("1"),
                    data_source=self.name,
                ),
            ]

    calls = {"alerts": 0}

    async def fake_upsert_daily_kline(_session, records):
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    async def fake_create_alert(*_args, **_kwargs):
        calls["alerts"] += 1

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)
    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    await sync_kline(
        StAwareSession(),
        ["000001.SZ"],
        date(2026, 5, 18),
        date(2026, 5, 19),
        providers=[SkippedQualityProvider()],
    )

    assert calls == {"alerts": 0}


async def test_select_sample_stock_codes_balances_across_code_segments() -> None:
    rows = [
        ("000001.SZ", "000001"),
        ("000002.SZ", "000002"),
        ("001200.SZ", "001200"),
        ("001201.SZ", "001201"),
        ("001202.SZ", "001202"),
        ("002001.SZ", "002001"),
        ("300001.SZ", "300001"),
        ("301001.SZ", "301001"),
        ("600000.SH", "600000"),
        ("601001.SH", "601001"),
        ("603000.SH", "603000"),
        ("605001.SH", "605001"),
    ]

    codes = await select_sample_stock_codes(StaticStockSession(rows), limit=6)

    assert codes == [
        "000001.SZ",
        "002001.SZ",
        "300001.SZ",
        "600000.SH",
        "603000.SH",
        "000002.SZ",
    ]


async def test_select_sample_stock_codes_fills_when_segments_are_sparse() -> None:
    rows = [
        ("001200.SZ", "001200"),
        ("001201.SZ", "001201"),
        ("001202.SZ", "001202"),
    ]

    assert await select_sample_stock_codes(StaticStockSession(rows), limit=20) == [
        "001200.SZ",
        "001201.SZ",
        "001202.SZ",
    ]


async def test_infer_incremental_kline_ranges_returns_only_symbols_with_tail_gaps() -> None:
    rows = [
        {
            "ts_code": "000001.SZ",
            "last_trade_date": date(2026, 5, 29),
            "start_date": None,
            "end_date": date(2026, 5, 29),
        },
        {
            "ts_code": "600000.SH",
            "last_trade_date": date(2026, 5, 15),
            "start_date": date(2026, 5, 18),
            "end_date": date(2026, 5, 29),
        },
    ]

    ranges = await infer_incremental_kline_ranges(IncrementalRangeSession(rows))

    assert ranges == [
        {
            "ts_code": "600000.SH",
            "last_trade_date": date(2026, 5, 15),
            "start_date": date(2026, 5, 18),
            "end_date": date(2026, 5, 29),
        }
    ]


async def test_infer_incremental_kline_ranges_qualifies_supported_stock_filter() -> None:
    session = IncrementalRangeSession([])

    await infer_incremental_kline_ranges(session)

    range_sql = session.statements[1]
    assert "COALESCE(sb.market" in range_sql
    assert "COALESCE(sb.exchange" in range_sql
    assert "split_part(sb.ts_code" in range_sql
    assert "split_part(ts_code" not in range_sql


async def test_infer_incremental_kline_ranges_starts_new_stock_from_list_date_open_day() -> None:
    rows = [
        {
            "ts_code": "001234.SZ",
            "last_trade_date": None,
            "start_date": date(2026, 5, 20),
            "end_date": date(2026, 5, 29),
        }
    ]

    ranges = await infer_incremental_kline_ranges(IncrementalRangeSession(rows))

    assert ranges[0]["ts_code"] == "001234.SZ"
    assert ranges[0]["last_trade_date"] is None
    assert ranges[0]["start_date"] == date(2026, 5, 20)


async def test_infer_incremental_kline_ranges_returns_empty_without_trade_calendar() -> None:
    ranges = await infer_incremental_kline_ranges(IncrementalRangeSession([], latest_open=None))

    assert ranges == []


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

    async def fake_backfill_stock_basic_market(_session):
        calls["backfill"] = calls.get("backfill", 0) + 1
        return 0

    async def fake_delete_unsupported_stock_data(_session):
        calls["delete"] = calls.get("delete", 0) + 1
        return {"stock_basic": 0}

    async def fake_record_update_success(*_args, **_kwargs):
        calls["success"] += 1

    async def fake_create_alert(*_args, **_kwargs):
        calls["alerts"] += 1

    monkeypatch.setattr(service, "upsert_stock_basic", fake_upsert_stock_basic)
    monkeypatch.setattr(service, "backfill_stock_basic_market", fake_backfill_stock_basic_market)
    monkeypatch.setattr(service, "delete_unsupported_stock_data", fake_delete_unsupported_stock_data)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)
    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    result = await sync_stock_basic(FakeSession(), providers=[MixedProvider()])

    assert result == {
        "source": "mixed",
        "inserted_or_updated": 1,
        "skipped": 1,
        "skipped_invalid": 1,
        "skipped_excluded": 0,
        "deleted_unsupported": {"stock_basic": 0},
    }
    assert calls == {"stock": 1, "alerts": 1, "success": 1, "backfill": 1, "delete": 1}


async def test_sync_stock_basic_skips_excluded_markets(monkeypatch) -> None:
    import app.data.service as service

    class MixedMarketProvider:
        name = "mixed-market"

        def fetch_stock_basic(self):
            return [
                StockBasic(ts_code="000001.SZ", symbol="000001", name="平安银行", market="主板", exchange="SZ"),
                StockBasic(ts_code="688001.SH", symbol="688001", name="华兴源创", market="科创板", exchange="SH"),
                StockBasic(ts_code="430001.BJ", symbol="430001", name="北交测试", market="北交所", exchange="BJ"),
                StockBasic(ts_code="200001.SZ", symbol="200001", name="深B测试", market="主板", exchange="SZ"),
                StockBasic(ts_code="900001.SH", symbol="900001", name="沪B测试", market="主板", exchange="SH"),
            ]

    captured = {"records": []}

    async def fake_upsert_stock_basic(_session, records):
        captured["records"] = records
        return len(records)

    async def fake_backfill_stock_basic_market(_session):
        return 0

    async def fake_delete_unsupported_stock_data(_session):
        return {"stock_basic": 3, "daily_kline": 5}

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "upsert_stock_basic", fake_upsert_stock_basic)
    monkeypatch.setattr(service, "backfill_stock_basic_market", fake_backfill_stock_basic_market)
    monkeypatch.setattr(service, "delete_unsupported_stock_data", fake_delete_unsupported_stock_data)
    monkeypatch.setattr(service, "record_update_success", fake_record_update_success)

    result = await sync_stock_basic(FakeSession(), providers=[MixedMarketProvider()])

    assert [record.ts_code for record in captured["records"]] == ["000001.SZ"]
    assert result["inserted_or_updated"] == 1
    assert result["skipped"] == 4
    assert result["skipped_excluded"] == 4
    assert result["deleted_unsupported"] == {"stock_basic": 3, "daily_kline": 5}


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
