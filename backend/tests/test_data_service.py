from datetime import date
from decimal import Decimal
import asyncio
import time
from types import SimpleNamespace

import pytest

from app.data.models import DailyKline, StockBasic
from app.data.service import infer_incremental_kline_ranges, select_sample_stock_codes, split_kline_ranges_by_year, sync_kline, sync_one_stock, sync_stock_basic

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

    def scalar_one(self):
        return self._rows[0][0]

    def scalar_one_or_none(self):
        return self._rows[0][0] if self._rows else None


class FakeSession:
    def __init__(self):
        self.select_calls = 0
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        # _bulk_load_is_st (batched) — return empty (no ST stocks → all default to False)
        if "COALESCE(is_st" in sql:
            return FakeResult([])
        # _bulk_load_failure_counts (batched) — return empty (no prior failures)
        if "MAX(failure_count)" in sql:
            return FakeResult([])
        if "SELECT COUNT(*) FROM stock_basic" in sql:
            return FakeResult([(1,)])
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
        # _bulk_load_is_st (batched) — return is_st for all requested ts_codes
        # as Row-like objects (support attribute access via .ts_code / .is_st).
        if "COALESCE(is_st" in sql:
            ts_codes = (params or {}).get("ts_codes", [])

            class _StRow:
                def __init__(self, ts_code, is_st):
                    self.ts_code = ts_code
                    self.is_st = is_st

            return FakeResult([_StRow(code, self.is_st) for code in ts_codes])
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
            # _bulk_load_is_st — return empty (no ST stocks → all default to False)
            if "COALESCE(is_st" in sql:
                return FakeResult([])
            # _bulk_load_failure_counts — return empty (no prior failures)
            if "MAX(failure_count)" in sql:
                return FakeResult([])
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
            # _bulk_load_is_st — return empty (no ST stocks → all default to False)
            if "COALESCE(is_st" in sql:
                return FakeResult([])
            # _bulk_load_failure_counts — return empty (no prior failures)
            if "MAX(failure_count)" in sql:
                return FakeResult([])
            if "SELECT ts_code" in sql:
                return FakeResult([("000001.SZ",), ("600000.SH",)])
            return FakeResult([])

    calls = {"commit": 0}

    async def fake_upsert_daily_kline(_session, records):
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)

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
            "end_date": date(2026, 5, 29)},
        {
            "ts_code": "600000.SH",
            "last_trade_date": date(2026, 5, 15),
            "start_date": date(2026, 5, 18),
            "end_date": date(2026, 5, 29)},
    ]

    ranges = await infer_incremental_kline_ranges(IncrementalRangeSession(rows))

    assert ranges == [
        {
            "ts_code": "600000.SH",
            "last_trade_date": date(2026, 5, 15),
            "start_date": date(2026, 5, 18),
            "end_date": date(2026, 5, 29)}
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
            "end_date": date(2026, 5, 29)}
    ]

    ranges = await infer_incremental_kline_ranges(IncrementalRangeSession(rows))

    assert ranges[0]["ts_code"] == "001234.SZ"
    assert ranges[0]["last_trade_date"] is None
    assert ranges[0]["start_date"] == date(2026, 5, 20)


async def test_infer_incremental_kline_ranges_returns_empty_without_trade_calendar() -> None:
    ranges = await infer_incremental_kline_ranges(IncrementalRangeSession([], latest_open=None))

    assert ranges == []


async def test_infer_incremental_kline_ranges_filters_out_delisted_stocks() -> None:
    """增量同步必须跳过已退市的股票，即使 is_delisted 状态未及时更新。

    过滤条件:
    1. is_delisted = FALSE (已有)
    2. delist_date IS NULL OR delist_date > end_date (新增)

    场景: stock_basic 同步在周六才跑，而增量 K 线每天 17:00 跑。
    如果股票在周中退市，is_delisted 可能还是 FALSE，但 delist_date
    已经设置且 <= end_date，应该跳过。
    """
    session = IncrementalRangeSession([])

    await infer_incremental_kline_ranges(session)

    range_sql = session.statements[1]
    # 必须同时检查 is_delisted 和 delist_date
    assert "is_delisted = FALSE" in range_sql
    assert "delist_date IS NULL" in range_sql
    assert "delist_date >" in range_sql


async def test_sync_stock_basic_skips_invalid_rows(monkeypatch) -> None:
    import app.data.service as service

    class MixedProvider:
        name = "mixed"

        def fetch_stock_basic(self):
            return [
                StockBasic(ts_code="000001.SZ", symbol="000001", name="平安银行"),
                StockBasic(ts_code="000002.SZ", symbol="000002", name=""),
            ]

    calls = {"stock": 0, "alerts": 0}

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
    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    result = await sync_stock_basic(FakeSession(), providers=[MixedProvider()])

    assert result == {
        "source": "mixed",
        "inserted_or_updated": 1,
        "total": 1,
        "skipped": 1,
        "skipped_invalid": 1,
        "skipped_excluded": 0,
        "deleted_unsupported": {"stock_basic": 0}}
    assert calls == {"stock": 1, "alerts": 1, "backfill": 1, "delete": 1}


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

    calls = { "alerts": 0}

    async def fake_record_update_failure(*_args, **_kwargs):
        calls["failure"] += 1

    async def fake_create_alert(*_args, **_kwargs):
        calls["alerts"] += 1

    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    with pytest.raises(ValueError, match="no valid records"):
        await sync_stock_basic(FakeSession(), providers=[BadProvider()])

    assert calls == { "alerts": 1}


async def test_sync_kline_commit_each_uses_fresh_session_for_alert_when_main_session_closed(monkeypatch) -> None:
    """Regression test: when commit_each=True and a main session is provided
    (e.g. via with_session wrapper in sync_sample_kline task), the main session
    is only used for batch queries (breaker + ST) at the entry and then sits
    idle while per-stock sessions do the loop work. If PostgreSQL closes the
    idle main-session connection (idle_in_transaction_session_timeout /
    keepalive failure), using it for the final alert INSERT would raise
    asyncpg InterfaceError: connection is closed.

    Fix: commit_each=True must record the alert via a fresh
    per_stock_session_factory() session, NOT the (potentially stale) main session.
    """
    import app.data.service as service

    class FailingProvider:
        name = "failing"

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            raise RuntimeError("simulated network failure")

    # Main session simulates a closed PostgreSQL connection — any execute
    # call after the initial batch queries must raise.
    class ClosedConnectionSession(StAwareSession):
        """First 2 queries (breaker + ST) succeed; subsequent queries raise
        InterfaceError('connection is closed') to simulate PG closing an
        idle connection during the per-stock loop."""

        def __init__(self):
            super().__init__()
            self.query_count = 0

        async def execute(self, statement, params=None):
            self.query_count += 1
            # First ~2 queries are the batch breaker + bulk_load_is_st
            # (only the ST query matches in StAwareSession; breaker is_open_batch
            # is mocked out below to return empty). After that, the connection
            # is "closed" by PostgreSQL.
            if self.query_count > 2:
                raise RuntimeError("connection is closed")
            return await super().execute(statement, params)

        async def commit(self):
            # Main session commit also fails — connection is closed
            raise RuntimeError("connection is closed")

    # Fresh session factory that returns a healthy session for alert recording
    class FreshSession(StAwareSession):
        """Fresh session that simulates a newly opened connection — all
        queries succeed."""

    fresh_sessions_created = 0

    class FreshSessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return FreshSession()

        async def __aexit__(self, *args):
            return False

    alert_sessions: list = []

    async def fake_create_alert(session, *args, **kwargs):
        # Only capture the "Daily kline sync completed with failures" alert
        # (not the per-stock quality alert which is a different code path)
        if kwargs.get("title") == "Daily kline sync completed with failures":
            alert_sessions.append(session)

    async def fake_record_update_failure(*_args, **_kwargs):
        return None

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    async def fake_filter_open_circuits(_session, providers, _data_type):
        return list(providers)

    monkeypatch.setattr(service, "create_alert", fake_create_alert)
    monkeypatch.setattr(service, "filter_open_circuits", fake_filter_open_circuits)

    # Use the closed-connection main session + fresh factory for per-stock work
    # Two stocks: one succeeds, one fails. The success keeps total > 0 so
    # sync_kline doesn't raise RuntimeError("all kline sync attempts failed").
    class MixedProvider:
        name = "mixed"

        def __init__(self):
            self.call_count = 0

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            self.call_count += 1
            if ts_code == "000002.SZ":
                raise RuntimeError("simulated network failure")
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

    async def fake_upsert_daily_kline(_session, records):
        return len(records)

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "create_alert", fake_create_alert)
    monkeypatch.setattr(service, "filter_open_circuits", fake_filter_open_circuits)

    closed_session = ClosedConnectionSession()
    result = await sync_kline(
        closed_session,
        ["000001.SZ", "000002.SZ"],
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[MixedProvider()],
        commit_each=True,
        concurrency=1,
        session_factory=FreshSessionFactory(),
    )

    # One stock succeeded, one failed
    assert result["inserted_or_updated"] == 1
    assert len(result["failures"]) == 1
    assert result["failures"][0]["ts_code"] == "000002.SZ"
    assert "simulated network failure" in result["failures"][0]["error"]
    # Alert was recorded via a FRESH session (not the stale main session).
    # If we had used the main session, create_alert would have raised
    # "connection is closed" and the task would have crashed.
    assert len(alert_sessions) == 1
    assert isinstance(alert_sessions[0], FreshSession)
    # Main session was NOT used for the alert (would have raised)
    assert not isinstance(alert_sessions[0], ClosedConnectionSession)


async def test_sync_kline_commit_each_false_still_uses_main_session_for_alert(monkeypatch) -> None:
    """When commit_each=False, the main session is actively used throughout
    the loop (not idle), so it's safe to use for the alert INSERT. This test
    verifies we didn't break that path."""
    import app.data.service as service

    # Two stocks: one succeeds, one fails. The success keeps total > 0 so
    # sync_kline doesn't raise RuntimeError("all kline sync attempts failed").
    class MixedProvider:
        name = "mixed"

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            if ts_code == "000002.SZ":
                raise RuntimeError("simulated network failure")
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

    alert_sessions: list = []

    async def fake_create_alert(session, *args, **kwargs):
        # Only capture the failure alert, not the per-stock quality alert
        if kwargs.get("title") == "Daily kline sync completed with failures":
            alert_sessions.append(session)

    async def fake_record_update_failure(*_args, **_kwargs):
        return None

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    async def fake_filter_open_circuits(_session, providers, _data_type):
        return list(providers)

    async def fake_upsert_daily_kline(_session, records):
        return len(records)

    monkeypatch.setattr(service, "create_alert", fake_create_alert)
    monkeypatch.setattr(service, "filter_open_circuits", fake_filter_open_circuits)
    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)

    main_session = StAwareSession()
    result = await sync_kline(
        main_session,
        ["000001.SZ", "000002.SZ"],
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[MixedProvider()],
        commit_each=False,
        concurrency=1,
    )

    assert result["inserted_or_updated"] == 1
    assert len(result["failures"]) == 1
    # commit_each=False → alert uses main session (still healthy)
    assert len(alert_sessions) == 1
    assert alert_sessions[0] is main_session


# ---------------------------------------------------------------------------
# split_kline_ranges_by_year
# ---------------------------------------------------------------------------


async def test_split_kline_ranges_by_year_keeps_single_year_range() -> None:
    """A range that starts and ends in the same year is returned unchanged."""
    ranges = [
        {
            "ts_code": "X",
            "start_date": date(2026, 3, 1),
            "end_date": date(2026, 7, 21)}
    ]

    split = split_kline_ranges_by_year(ranges)

    assert split == ranges
    assert len(split) == 1


async def test_split_kline_ranges_by_year_splits_multi_year_range() -> None:
    """A range spanning multiple calendar years is split at year boundaries."""
    ranges = [
        {
            "ts_code": "X",
            "start_date": date(2024, 3, 15),
            "end_date": date(2026, 7, 21)}
    ]

    split = split_kline_ranges_by_year(ranges)

    assert len(split) == 3
    # 2024 partial year
    assert split[0]["start_date"] == date(2024, 3, 15)
    assert split[0]["end_date"] == date(2024, 12, 31)
    # 2025 full year
    assert split[1]["start_date"] == date(2025, 1, 1)
    assert split[1]["end_date"] == date(2025, 12, 31)
    # 2026 partial year
    assert split[2]["start_date"] == date(2026, 1, 1)
    assert split[2]["end_date"] == date(2026, 7, 21)
    # ts_code preserved on every sub-range
    assert all(r["ts_code"] == "X" for r in split)


async def test_split_kline_ranges_by_year_preserves_other_fields() -> None:
    """Extra fields (e.g. last_trade_date) are propagated to every sub-range."""
    ranges = [
        {
            "ts_code": "X",
            "start_date": date(2024, 3, 15),
            "end_date": date(2026, 7, 21),
            "last_trade_date": date(2024, 3, 14)}
    ]

    split = split_kline_ranges_by_year(ranges)

    assert len(split) == 3
    for sub in split:
        assert sub["last_trade_date"] == date(2024, 3, 14)
        assert sub["ts_code"] == "X"

# ---------------------------------------------------------------------------
# sync_kline — configurable per-stock timeout
# ---------------------------------------------------------------------------


async def test_sync_kline_uses_configurable_per_stock_timeout(monkeypatch) -> None:
    """When settings.kline_per_stock_timeout_seconds is small, a slow provider
    is killed by asyncio.TimeoutError and recorded as a failure (not a hang)."""
    import app.data.service as service

    # 1s timeout — the slow provider sleeps 2s, so it will time out.
    fake_settings = SimpleNamespace(
        kline_per_stock_timeout_seconds=1,
        kline_permanent_failure_threshold=50,
    )
    monkeypatch.setattr(service, "get_settings", lambda: fake_settings)

    class FastProvider:
        name = "fast"

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

    class SlowProvider:
        name = "slow"

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            time.sleep(2)
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

    # Route 000001.SZ → fast, 000002.SZ → slow (times out)
    class RoutingProvider:
        name = "router"

        def __init__(self):
            self._fast = FastProvider()
            self._slow = SlowProvider()

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            if ts_code == "000002.SZ":
                return self._slow.fetch_daily_kline(ts_code, start_date, end_date)
            return self._fast.fetch_daily_kline(ts_code, start_date, end_date)

    async def fake_upsert_daily_kline(_session, records):
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        return None

    async def fake_record_update_failure(*_args, **_kwargs):
        return None

    async def fake_filter_open_circuits(_session, providers, _data_type):
        return list(providers)

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "filter_open_circuits", fake_filter_open_circuits)
    monkeypatch.setattr(service, "create_alert", fake_record_update_failure)

    result = await sync_kline(
        StAwareSession(),
        ["000001.SZ", "000002.SZ"],
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[RoutingProvider()],
        commit_each=False,
        concurrency=1,
    )

    # One success (000001.SZ), one timeout failure (000002.SZ)
    assert result["inserted_or_updated"] == 1
    failures_by_code = {f["ts_code"]: f for f in result["failures"]}
    assert "000002.SZ" in failures_by_code
    # 000001.SZ (fast provider) must NOT be in failures
    assert "000001.SZ" not in failures_by_code


# ---------------------------------------------------------------------------
# sync_one_stock
# ---------------------------------------------------------------------------


class _SyncOneStockSessionFactory:
    """Session factory that returns a fresh ``StAwareSession`` per call.

    Mirrors the ``FreshSessionFactory`` pattern used by the commit_each
    regression tests: callable + async context manager in one object.
    """

    def __init__(self, *, is_st: bool = False):
        self.is_st = is_st

    def __call__(self):
        return self

    async def __aenter__(self):
        return StAwareSession(is_st=self.is_st)

    async def __aexit__(self, *args):
        return False


async def test_sync_one_stock_success(monkeypatch) -> None:
    """A successful sync: provider returns valid kline, upsert is called,
    record_update_success is called, and the result has success=True."""
    import app.data.service as service

    class SuccessProvider:
        name = "fake"

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

    calls = {"upsert": 0}

    async def fake_upsert_daily_kline(_session, records):
        calls["upsert"] += len(records)
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        calls["success"] += 1

    async def fake_record_update_failure(*_args, **_kwargs):
        calls["failure"] += 1

    async def fake_filter_open_circuits(_session, providers, _data_type):
        return list(providers)

    async def fake_create_alert(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "filter_open_circuits", fake_filter_open_circuits)
    monkeypatch.setattr(service, "create_alert", fake_create_alert)

    result = await sync_one_stock(
        _SyncOneStockSessionFactory(),
        "000001.SZ",
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[SuccessProvider()],
    )

    assert result == {"success": True, "error": None, "source": "fake", "synced": 1}
    assert calls == {"upsert": 1}


async def test_sync_one_stock_fetch_fails(monkeypatch) -> None:
    """When the provider raises, record_update_failure is called and the
    result has success=False with the error message."""
    import app.data.service as service

    class FailingProvider:
        name = "failing"

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            raise RuntimeError("simulated network failure")

    calls = {"upsert": 0}

    async def fake_upsert_daily_kline(_session, records):
        calls["upsert"] += len(records)
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        calls["success"] += 1

    async def fake_record_update_failure(*_args, **_kwargs):
        calls["failure"] += 1

    async def fake_filter_open_circuits(_session, providers, _data_type):
        return list(providers)

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "filter_open_circuits", fake_filter_open_circuits)
    monkeypatch.setattr(service, "create_alert", fake_record_update_failure)

    result = await sync_one_stock(
        _SyncOneStockSessionFactory(),
        "000001.SZ",
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[FailingProvider()],
    )

    assert result["success"] is False
    assert "simulated network failure" in result["error"]
    assert result["source"] is None
    assert result["synced"] == 0
    assert calls == {"upsert": 0}


async def test_sync_one_stock_timeout(monkeypatch) -> None:
    """When the provider sleeps longer than per_stock_timeout, the fetch is
    cancelled by asyncio.TimeoutError and the result has success=False."""
    import app.data.service as service

    class SlowProvider:
        name = "slow"

        def fetch_daily_kline(self, ts_code, start_date, end_date):
            time.sleep(2)
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

    calls = {"upsert": 0}

    async def fake_upsert_daily_kline(_session, records):
        calls["upsert"] += len(records)
        return len(records)

    async def fake_record_update_success(*_args, **_kwargs):
        calls["success"] += 1

    async def fake_record_update_failure(*_args, **_kwargs):
        calls["failure"] += 1

    async def fake_filter_open_circuits(_session, providers, _data_type):
        return list(providers)

    monkeypatch.setattr(service, "upsert_daily_kline", fake_upsert_daily_kline)
    monkeypatch.setattr(service, "filter_open_circuits", fake_filter_open_circuits)
    monkeypatch.setattr(service, "create_alert", fake_record_update_failure)

    result = await sync_one_stock(
        _SyncOneStockSessionFactory(),
        "000001.SZ",
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[SlowProvider()],
        per_stock_timeout=1,
    )

    assert result["success"] is False
    assert result["source"] is None
    assert result["synced"] == 0
    assert calls == {"upsert": 0}

