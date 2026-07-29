from datetime import date
from decimal import Decimal
import importlib.util
import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.data.models import StockFundamental
from app.data.normalizers import normalize_stock_fundamental
from app.data.repository import upsert_stock_fundamentals
from app.data.stock_service import (
    StockFilters,
    add_watchlist_items_batch,
    add_watchlist_item,
    create_watchlist_group,
    delete_watchlist_group,
    delete_watchlist_item,
    list_watchlist_groups,
    list_stocks,
    rename_watchlist_group,
    stock_name_initials,
    sync_fundamentals,
    update_watchlist_item,
)
from app.db.session import get_session
from app.main import app

class FakeResult:
    def __init__(self, rows=None, scalar=None, rowcount=1):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar if self._scalar is not None else self._rows[0]

    def scalar_one_or_none(self):
        return self._scalar


class CaptureSession:
    def __init__(self, results=None):
        self.statements = []
        self.params = []
        self.commits = 0
        self.results = list(results or [])

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        if self.results:
            return self.results.pop(0)
        return FakeResult([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


def test_m2_migration_contains_stock_management_tables_and_local_user_seed() -> None:
    spec = importlib.util.spec_from_file_location(
        "m2_migration",
        "backend/alembic/versions/202605180001_m2_stock_management.py",
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    sql = "\n".join(migration.UPGRADE_STATEMENTS)

    assert "INSERT INTO users (id, username" in sql
    assert "stock_fundamentals" in sql
    assert "watchlist" in sql
    assert "stock_pools" in sql
    assert "stock_pool_items" in sql
    assert "UNIQUE (user_id, group_name, ts_code)" in sql
    assert "PRIMARY KEY (pool_id, ts_code)" in sql


def test_normalize_stock_fundamental_maps_aliases_and_missing_fields() -> None:
    record = normalize_stock_fundamental(
        {
            "code": "sh.600000",
            "date": "2026-05-18",
            "peTTM": "8.25",
            "pbMRQ": "0.72",
            "市销率-TTM": "",
            "总市值": "123456.78",
        },
        "baostock",
    )

    assert record.ts_code == "600000.SH"
    assert record.report_date == date(2026, 5, 18)
    assert record.pe_ttm == Decimal("8.25")
    assert record.pb == Decimal("0.72")
    assert record.ps_ttm is None
    assert record.market_cap == Decimal("123456.78")


@pytest.mark.asyncio
async def test_upsert_stock_fundamentals_is_idempotent_and_coalesces_nulls() -> None:
    session = CaptureSession()

    count = await upsert_stock_fundamentals(
        session,
        [
            StockFundamental(
                ts_code="000001.SZ",
                report_date=date(2026, 5, 18),
                pe_ttm=Decimal("9.1"),
                income_statement={"营业收入": "1"},
            )
        ],
    )

    assert count == 1
    assert session.params[0][0]["ts_code"] == "000001.SZ"
    assert json.loads(session.params[0][0]["income_statement"]) == {"营业收入": "1"}
    assert "ON CONFLICT (ts_code, report_date)" in session.statements[0]
    assert "pe_ttm = COALESCE(EXCLUDED.pe_ttm, stock_fundamentals.pe_ttm)" in session.statements[0]


@pytest.mark.asyncio
async def test_list_stocks_filters_exclude_st_and_null_numeric_ranges() -> None:
    session = CaptureSession(
        [
            FakeResult(scalar=1),
            FakeResult(
                [
                    {
                        "ts_code": "000001.SZ",
                        "symbol": "000001",
                        "name": "平安银行",
                        "pe_ttm": Decimal("8.1"),
                    }
                ]
            ),
        ]
    )

    result = await list_stocks(
        session,
        StockFilters(exclude_st=True, industry="银行", pe_min=Decimal("0"), pe_max=Decimal("12")),
    )

    sql = "\n".join(session.statements)
    assert result["total"] == 1
    assert "s.is_st = FALSE" in sql
    assert "s.industry = :industry" in sql
    assert "f.pe_ttm IS NOT NULL" in sql
    assert session.params[0]["pe_min"] == Decimal("0")
    assert session.params[0]["pe_max"] == Decimal("12")


@pytest.mark.asyncio
async def test_list_stocks_filters_by_market_segments() -> None:
    session = CaptureSession(
        [
            FakeResult(scalar=1),
            FakeResult([{"ts_code": "300001.SZ", "symbol": "300001", "name": "测试股票"}]),
        ]
    )

    await list_stocks(session, StockFilters(market=["创业板", "科创板"]))

    sql = "\n".join(session.statements)
    assert "s.market IN (:market_0, :market_1)" in sql
    assert session.params[0]["market_0"] == "创业板"
    assert session.params[0]["market_1"] == "科创板"


def test_stock_name_initials_supports_common_a_share_names() -> None:
    assert stock_name_initials("纳百川") == "nbc"
    assert stock_name_initials("招商银行") == "zsyh"
    assert stock_name_initials("中国平安") == "zgpa"


@pytest.mark.asyncio
async def test_list_stocks_query_matches_chinese_initials_and_code_fuzzy() -> None:
    rows = [
        {"ts_code": "301667.SZ", "symbol": "301667", "name": "纳百川", "market": "创业板"},
        {"ts_code": "600036.SH", "symbol": "600036", "name": "招商银行", "market": "主板"},
        {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "market": "主板"},
    ]
    session = CaptureSession([FakeResult(rows)])

    initials_result = await list_stocks(session, StockFilters(query="nbc"))

    assert initials_result["total"] == 1
    assert initials_result["items"][0]["ts_code"] == "301667.SZ"

    code_session = CaptureSession([FakeResult(rows)])
    code_result = await list_stocks(code_session, StockFilters(query="0036"))

    assert code_result["total"] == 1
    assert code_result["items"][0]["ts_code"] == "600036.SH"


@pytest.mark.asyncio
async def test_list_stocks_query_matches_chinese_name_fuzzy() -> None:
    session = CaptureSession(
        [
            FakeResult(
                [
                    {"ts_code": "301667.SZ", "symbol": "301667", "name": "纳百川"},
                    {"ts_code": "600036.SH", "symbol": "600036", "name": "招商银行"},
                ]
            )
        ]
    )

    result = await list_stocks(session, StockFilters(query="银行"))

    assert result["total"] == 1
    assert result["items"][0]["name"] == "招商银行"


@pytest.mark.asyncio
async def test_watchlist_add_update_delete_flow() -> None:
    session = CaptureSession(
        [
            FakeResult(scalar=1),
            FakeResult(),
            FakeResult([{"id": 3, "group_name": "价值", "ts_code": "000001.SZ", "note": "low pe", "sort_order": 2}]),
            FakeResult([{"id": 3, "group_name": "银行", "ts_code": "000001.SZ", "note": "low pe", "sort_order": 1}]),
            FakeResult(rowcount=1),
        ]
    )

    added = await add_watchlist_item(session, ts_code="000001.SZ", group_name="价值", note="low pe", sort_order=2)
    updated = await update_watchlist_item(session, 3, group_name="银行", sort_order=1)
    deleted = await delete_watchlist_item(session, 3)

    assert added["id"] == 3
    assert updated["group_name"] == "银行"
    assert deleted is True
    assert "ON CONFLICT (user_id, group_name) DO NOTHING" in session.statements[1]
    assert "ON CONFLICT (user_id, group_name, ts_code)" in session.statements[2]
    assert "group_name = :group_name" in session.statements[3]


@pytest.mark.asyncio
async def test_watchlist_group_summary_lists_group_counts() -> None:
    session = CaptureSession(
        [
            FakeResult(
                [
                    {"group_name": "默认", "item_count": 2},
                    {"group_name": "成长", "item_count": 1},
                ]
            )
        ]
    )

    groups = await list_watchlist_groups(session)

    assert groups == [
        {"group_name": "默认", "item_count": 2},
        {"group_name": "成长", "item_count": 1},
    ]


@pytest.mark.asyncio
async def test_watchlist_group_create_rename_delete_flow_moves_items_to_default() -> None:
    session = CaptureSession(
        [
            FakeResult([{"id": 5, "group_name": "价投"}]),
            FakeResult(scalar=None),
            FakeResult([{"id": 5, "group_name": "观察股"}]),
            FakeResult(),
            FakeResult(),
            FakeResult(),
            FakeResult(),
            FakeResult(rowcount=1),
        ]
    )

    created = await create_watchlist_group(session, "价投")
    renamed = await rename_watchlist_group(session, "价投", "观察股")
    deleted = await delete_watchlist_group(session, "观察股")

    assert created["group_name"] == "价投"
    assert renamed is not None
    assert renamed["group_name"] == "观察股"
    assert deleted is True
    sql = "\n".join(session.statements)
    assert "CREATE TABLE" not in sql
    assert "UPDATE watchlist_groups" in sql
    assert "UPDATE watchlist" in sql
    assert "target.group_name = '默认'" in sql
    assert "SET group_name = '默认'" in sql


@pytest.mark.asyncio
async def test_watchlist_group_rename_rejects_existing_name() -> None:
    session = CaptureSession([FakeResult(scalar=1)])

    with pytest.raises(ValueError, match="already exists"):
        await rename_watchlist_group(session, "价投", "观察股")


@pytest.mark.asyncio
async def test_watchlist_group_delete_rejects_default() -> None:
    session = CaptureSession()

    with pytest.raises(ValueError, match="default watchlist group"):
        await delete_watchlist_group(session, "默认")


@pytest.mark.asyncio
async def test_watchlist_rejects_unknown_ts_code() -> None:
    session = CaptureSession([FakeResult(scalar=None)])

    with pytest.raises(ValueError, match="unknown ts_code"):
        await add_watchlist_item(session, ts_code="000001.SZ")


@pytest.mark.asyncio
async def test_watchlist_batch_creates_group_dedupes_and_keeps_unknown_errors() -> None:
    session = CaptureSession(
        [
            FakeResult([{"ts_code": "000001.SZ"}, {"ts_code": "600000.SH"}]),
            FakeResult(),
            FakeResult([{"id": 10, "group_name": "强势股", "ts_code": "000001.SZ", "note": "signals", "sort_order": 0}]),
            FakeResult([{"id": 11, "group_name": "强势股", "ts_code": "600000.SH", "note": "signals", "sort_order": 1}]),
        ]
    )

    result = await add_watchlist_items_batch(
        session,
        ts_codes=["000001.SZ", "000001.sz", "600000.SH", "999999.SH"],
        group_name=" 强势股 ",
        note="signals",
    )

    assert result["group_name"] == "强势股"
    assert result["added_count"] == 2
    assert result["skipped_count"] == 1
    assert [item["ts_code"] for item in result["items"]] == ["000001.SZ", "600000.SH"]
    assert result["errors"] == [{"ts_code": "999999.SH", "error": "unknown ts_code"}]
    assert session.commits == 1

    sql = "\n".join(session.statements)
    assert "INSERT INTO watchlist_groups" in sql
    assert "ON CONFLICT (user_id, group_name, ts_code)" in sql
    assert session.params[2]["group_name"] == "强势股"
    assert session.params[2]["note"] == "signals"


@pytest.mark.asyncio
async def test_watchlist_batch_rejects_blank_group() -> None:
    session = CaptureSession()

    with pytest.raises(ValueError, match="group_name is required"):
        await add_watchlist_items_batch(session, ts_codes=["000001.SZ"], group_name=" ")


@pytest.mark.asyncio
async def test_sync_fundamentals_allows_partial_failures(monkeypatch) -> None:
    import app.data.stock_service as service

    class Provider:
        name = "baostock"

        def fetch_stock_fundamentals(self, ts_codes, start_date, end_date):
            if ts_codes == ["600000.SH"]:
                raise RuntimeError("offline")
            return [StockFundamental(ts_code=ts_codes[0], report_date=start_date, pe_ttm=Decimal("9"))]

    calls = {"upsert": 0, "alerts": 0}

    async def fake_upsert(_session, records):
        calls["upsert"] += len(records)
        return len(records)

    async def fake_alert(*_args, **_kwargs):
        calls["alerts"] += 1

    monkeypatch.setattr(service, "upsert_stock_fundamentals", fake_upsert)
    monkeypatch.setattr(service, "create_alert", fake_alert)

    result = await sync_fundamentals(
        CaptureSession(),
        ["000001.SZ", "600000.SH"],
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[Provider()],
    )

    assert result["inserted_or_updated"] == 1
    assert result["failures"][0]["ts_code"] == "600000.SH"
    assert calls == {"upsert": 1, "alerts": 1}


@pytest.mark.asyncio
async def test_sync_fundamentals_commit_each_uses_per_stock_sessions(monkeypatch) -> None:
    import app.data.stock_service as service

    class Provider:
        name = "eastmoney"

        def fetch_stock_fundamentals(self, ts_codes, start_date, end_date):
            if ts_codes == ["600000.SH"]:
                raise RuntimeError("offline")
            return [StockFundamental(ts_code=ts_codes[0], report_date=start_date, pe_ttm=Decimal("9"))]

    class SessionFactory:
        def __init__(self):
            self.sessions: list[CaptureSession] = []

        def __call__(self):
            factory = self

            class Context:
                async def __aenter__(self):
                    session = CaptureSession()
                    factory.sessions.append(session)
                    return session

                async def __aexit__(self, exc_type, exc, traceback):
                    return None

            return Context()

    session_factory = SessionFactory()
    calls = {"upsert": 0, "alerts": 0}
    progress: list[tuple[int, int, str]] = []

    async def fake_upsert(_session, records):
        calls["upsert"] += len(records)
        return len(records)

    async def fake_alert(*_args, **_kwargs):
        calls["alerts"] += 1

    monkeypatch.setattr(service, "async_session_factory", session_factory)
    monkeypatch.setattr(service, "upsert_stock_fundamentals", fake_upsert)
    monkeypatch.setattr(service, "create_alert", fake_alert)

    result = await sync_fundamentals(
        None,
        ["000001.SZ", "600000.SH"],
        date(2026, 5, 18),
        date(2026, 5, 18),
        providers=[Provider()],
        progress_callback=lambda current, total, code: progress.append((current, total, code)),
        commit_each=True,
        concurrency=2,
    )

    assert result["requested_symbols"] == 2
    assert result["inserted_or_updated"] == 1
    assert result["failures"] == [{"ts_code": "600000.SH", "error": "eastmoney: offline"}]
    assert calls == {"upsert": 1, "alerts": 1}
    assert progress == [(1, 2, "000001.SZ"), (2, 2, "600000.SH")]
    assert [session.commits for session in session_factory.sessions] == [1, 1, 1]


def test_fundamentals_task_writes_pending_task_run(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = CaptureSession([FakeResult([1])])

    async def override_session():
        yield fake_session

    async def guard_noop(_session):
        return None

    monkeypatch.setattr(task_api, "_guard_fundamentals_sync", guard_noop)
    monkeypatch.setattr(task_api, "get_full_kline_sync_concurrency", AsyncMock(return_value=2))
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "task-456"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_fundamentals_task, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={"ts_codes": ["000001.SZ"]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-456", "status": "pending"}
    assert fake_session.params[0]["task_name"] == "sync_fundamentals"
    apply_async.assert_called_once_with(
        kwargs={"ts_codes": ["000001.SZ"], "start_date": None, "end_date": None, "concurrency": 2},
        task_id="task-456",
    )
