import asyncio
from datetime import date, datetime

import pytest
from sqlalchemy import text

from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked
from app.tasks.trading_tasks import _is_realtime_trading_time, _is_open_trade_day, _snapshot_nav_daily


class _FakeFactory:
    """Wraps a session so it can be passed as ``session_factory`` to fn.

    Mimics ``async_session_factory()`` returning an async context manager
    that yields ``session``. Optionally closes the session on exit to mirror
    real session lifecycle (used by tests that assert ``session.closed``).
    """

    def __init__(self, session, *, close_on_exit: bool = True):
        self._session = session
        self._close_on_exit = close_on_exit

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        if self._close_on_exit and hasattr(self._session, "close"):
            await self._session.close()
        return False


def test_celery_app_registers_data_tasks() -> None:
    celery_app.loader.import_default_modules()

    registered = set(celery_app.tasks)

    assert "app.tasks.data_tasks.update_stock_basic" in registered
    assert "app.tasks.data_tasks.update_trade_calendar" in registered
    assert "app.tasks.data_tasks.sync_sample_kline" in registered
    assert "app.tasks.data_tasks.incremental_kline_update" in registered
    assert "app.tasks.trading_tasks.unlock_t1_daily" in registered
    assert "app.tasks.trading_tasks.match_pending_orders" in registered
    assert "app.tasks.trading_tasks.snapshot_nav_daily" in registered
    assert "app.tasks.signal_tasks.generate_all_signals" in registered
    assert celery_app.conf.beat_schedule["generate-signals-daily"]["task"] == "app.tasks.signal_tasks.generate_all_signals"
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_sync_fundamentals_task_defaults_to_all_codes_and_reports_progress(monkeypatch) -> None:
    from app.tasks import data_tasks

    class FakeSession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    fake_session = FakeSession()
    captured = {}
    updates = []

    async def fake_run_tracked(task_name, task_id, payload, fn):
        captured["tracked"] = {"task_name": task_name, "task_id": task_id, "payload": payload}
        return await fn(_FakeFactory(fake_session))

    async def fake_select_all_stock_codes(session):
        captured["select_session"] = session
        return ["000001.SZ", "600000.SH"]

    async def fake_sync_fundamentals(
        session,
        ts_codes,
        start_date,
        end_date,
        providers=None,
        progress_callback=None,
        commit_each=False,
        concurrency=1,
    ):
        captured["sync"] = {
            "session": session,
            "ts_codes": ts_codes,
            "start_date": start_date,
            "end_date": end_date,
            "providers": providers,
            "commit_each": commit_each,
            "concurrency": concurrency,
        }
        progress_callback(1, len(ts_codes), ts_codes[0])
        progress_callback(2, len(ts_codes), ts_codes[1])
        return {
            "requested_symbols": len(ts_codes),
            "inserted_or_updated": 2,
            "source_counts": {"eastmoney": 2},
            "failures": [],
            "start_date": start_date,
            "end_date": end_date,
        }

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "select_all_stock_codes", fake_select_all_stock_codes)
    monkeypatch.setattr(data_tasks, "sync_fundamentals", fake_sync_fundamentals)
    monkeypatch.setattr(data_tasks.sync_fundamentals_task, "update_state", lambda **kwargs: updates.append(kwargs))

    result = data_tasks.sync_fundamentals_task.run(start_date="2026-05-01", end_date="2026-05-02")

    assert result["requested_symbols"] == 2
    assert captured["tracked"]["task_name"] == "sync_fundamentals"
    assert captured["tracked"]["payload"]["ts_codes"] is None
    assert captured["tracked"]["payload"]["concurrency"] == 2
    assert captured["select_session"] is fake_session
    assert fake_session.closed is True
    assert captured["sync"]["session"] is None
    assert captured["sync"]["ts_codes"] == ["000001.SZ", "600000.SH"]
    assert captured["sync"]["commit_each"] is True
    assert captured["sync"]["concurrency"] == 2
    assert updates == [
        {"state": "PROGRESS", "meta": {"current": 1, "total": 2, "current_code": "000001.SZ"}},
        {"state": "PROGRESS", "meta": {"current": 2, "total": 2, "current_code": "600000.SH"}},
    ]


def test_sync_fundamentals_task_honors_explicit_concurrency(monkeypatch) -> None:
    from app.tasks import data_tasks

    class FakeSession:
        async def close(self):
            return None

    captured = {}

    async def fake_run_tracked(task_name, task_id, payload, fn):
        captured["payload"] = payload
        return await fn(_FakeFactory(FakeSession()))

    async def fake_select_all_stock_codes(session):
        return ["000001.SZ"]

    async def fake_sync_fundamentals(
        session,
        ts_codes,
        start_date,
        end_date,
        providers=None,
        progress_callback=None,
        commit_each=False,
        concurrency=1,
    ):
        captured["concurrency"] = concurrency
        return {"requested_symbols": 1, "inserted_or_updated": 1, "source_counts": {}, "failures": []}

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "select_all_stock_codes", fake_select_all_stock_codes)
    monkeypatch.setattr(data_tasks, "sync_fundamentals", fake_sync_fundamentals)

    result = data_tasks.sync_fundamentals_task.run(
        start_date="2026-05-01",
        end_date="2026-05-02",
        concurrency=4,
    )

    assert result["requested_symbols"] == 1
    assert captured["payload"]["concurrency"] == 4
    assert captured["concurrency"] == 4


def test_sync_sample_kline_task_honors_concurrency(monkeypatch) -> None:
    from app.tasks import data_tasks

    captured = {}

    class _NullSession:
        async def close(self):
            return None

    async def fake_run_tracked(task_name, task_id, payload, fn):
        captured["payload"] = payload
        return await fn(_FakeFactory(_NullSession()))

    async def fake_sync_kline(
        session,
        ts_codes,
        start_date,
        end_date,
        providers=None,
        progress_callback=None,
        commit_each=False,
        concurrency=1,
    ):
        captured["sync"] = {"commit_each": commit_each, "concurrency": concurrency}
        return {"requested_symbols": 1, "inserted_or_updated": 1, "source_counts": {}, "failures": []}

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "sync_kline", fake_sync_kline)

    result = data_tasks.sync_sample_kline.run(
        ts_codes=["000001.SZ"],
        start_date="2026-05-01",
        end_date="2026-05-02",
        concurrency=3,
    )

    assert result["requested_symbols"] == 1
    assert captured["payload"]["concurrency"] == 3
    assert captured["sync"] == {"commit_each": True, "concurrency": 3}


def test_incremental_kline_update_uses_default_concurrency(monkeypatch) -> None:
    from app.tasks import data_tasks

    class FakeSession:
        async def close(self):
            return None

    captured = {}

    async def fake_run_tracked(task_name, task_id, payload, fn):
        captured["payload"] = payload
        return await fn(_FakeFactory(FakeSession()))

    async def fake_infer_incremental_kline_ranges(session):
        return [
            {
                "ts_code": "000001.SZ",
                "start_date": date(2026, 5, 1),
                "end_date": date(2026, 5, 2),
                "last_trade_date": date(2026, 4, 30),
            }
        ]

    async def fake_select_all_stock_codes(session):
        return ["000001.SZ", "600000.SH"]

    async def fake_sync_kline(
        session,
        ts_codes,
        start_date,
        end_date,
        providers=None,
        progress_callback=None,
        commit_each=False,
        concurrency=1,
    ):
        captured["sync"] = {
            "session": session,
            "ts_codes": ts_codes,
            "start_date": start_date,
            "end_date": end_date,
            "commit_each": commit_each,
            "concurrency": concurrency,
        }
        return {"requested_symbols": 1, "inserted_or_updated": 1, "source_counts": {}, "failures": []}

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "infer_incremental_kline_ranges", fake_infer_incremental_kline_ranges)
    monkeypatch.setattr(data_tasks, "select_all_stock_codes", fake_select_all_stock_codes)
    monkeypatch.setattr(data_tasks, "sync_kline", fake_sync_kline)

    result = data_tasks.incremental_kline_update.run()

    assert result["requested_symbols"] == 1
    assert result["gap_symbols"] == 1
    assert result["skipped_symbols"] == 1
    assert captured["payload"]["concurrency"] == 2
    assert captured["sync"] == {
        "session": None,
        "ts_codes": ["000001.SZ"],
        "start_date": date(2026, 5, 1),
        "end_date": date(2026, 5, 2),
        "commit_each": True,
        "concurrency": 2,
    }


def test_incremental_kline_update_groups_per_stock_ranges_and_reports_progress(monkeypatch) -> None:
    from app.tasks import data_tasks

    class FakeSession:
        async def close(self):
            return None

    updates = []
    sync_calls = []

    async def fake_run_tracked(_task_name, _task_id, _payload, fn):
        return await fn(_FakeFactory(FakeSession()))

    async def fake_infer_incremental_kline_ranges(_session):
        return [
            {
                "ts_code": "000001.SZ",
                "start_date": date(2026, 5, 1),
                "end_date": date(2026, 5, 2),
                "last_trade_date": date(2026, 4, 30),
            },
            {
                "ts_code": "000002.SZ",
                "start_date": date(2026, 5, 1),
                "end_date": date(2026, 5, 2),
                "last_trade_date": date(2026, 4, 30),
            },
            {
                "ts_code": "600000.SH",
                "start_date": date(2026, 5, 2),
                "end_date": date(2026, 5, 2),
                "last_trade_date": date(2026, 5, 1),
            },
        ]

    async def fake_select_all_stock_codes(_session):
        return ["000001.SZ", "000002.SZ", "600000.SH", "600001.SH"]

    async def fake_sync_kline(
        session,
        ts_codes,
        start_date,
        end_date,
        providers=None,
        progress_callback=None,
        commit_each=False,
        concurrency=1,
    ):
        sync_calls.append((ts_codes, start_date, end_date, commit_each, concurrency))
        for index, code in enumerate(ts_codes, start=1):
            progress_callback(index, len(ts_codes), code)
        return {
            "requested_symbols": len(ts_codes),
            "inserted_or_updated": len(ts_codes),
            "source_counts": {"fake": len(ts_codes)},
            "failures": [],
        }

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "infer_incremental_kline_ranges", fake_infer_incremental_kline_ranges)
    monkeypatch.setattr(data_tasks, "select_all_stock_codes", fake_select_all_stock_codes)
    monkeypatch.setattr(data_tasks, "sync_kline", fake_sync_kline)
    monkeypatch.setattr(data_tasks.incremental_kline_update, "update_state", lambda **kwargs: updates.append(kwargs))

    result = data_tasks.incremental_kline_update.run(concurrency=3)

    assert sync_calls == [
        (["000001.SZ", "000002.SZ"], date(2026, 5, 1), date(2026, 5, 2), True, 3),
        (["600000.SH"], date(2026, 5, 2), date(2026, 5, 2), True, 3),
    ]
    assert result["requested_symbols"] == 3
    assert result["skipped_symbols"] == 1
    assert result["inserted_or_updated"] == 3
    assert result["source_counts"] == {"fake": 3}
    assert updates == [
        {"state": "PROGRESS", "meta": {"current": 1, "total": 3, "current_code": "000001.SZ"}},
        {"state": "PROGRESS", "meta": {"current": 2, "total": 3, "current_code": "000002.SZ"}},
        {"state": "PROGRESS", "meta": {"current": 3, "total": 3, "current_code": "600000.SH"}},
    ]


def test_incremental_kline_update_skips_when_no_per_stock_gaps(monkeypatch) -> None:
    from app.tasks import data_tasks

    class FakeSession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    fake_session = FakeSession()

    async def fake_run_tracked(_task_name, _task_id, _payload, fn):
        return await fn(_FakeFactory(fake_session))

    async def fake_infer_incremental_kline_ranges(_session):
        return []

    async def fake_select_all_stock_codes(_session):
        return ["000001.SZ", "600000.SH"]

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "infer_incremental_kline_ranges", fake_infer_incremental_kline_ranges)
    monkeypatch.setattr(data_tasks, "select_all_stock_codes", fake_select_all_stock_codes)

    result = data_tasks.incremental_kline_update.run()

    assert result["skipped"] is True
    assert result["requested_symbols"] == 0
    assert result["skipped_symbols"] == 2
    assert fake_session.closed is True


def test_sync_all_kline_task_uses_default_concurrency_and_tracks_payload(monkeypatch) -> None:
    from app.tasks import data_tasks

    class FakeSession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    fake_session = FakeSession()
    captured = {}
    updates = []

    async def fake_run_tracked(task_name, task_id, payload, fn):
        captured["tracked"] = {"task_name": task_name, "task_id": task_id, "payload": payload}
        return await fn(_FakeFactory(fake_session))

    async def fake_select_all_stock_codes(session):
        captured["select_session"] = session
        return ["000001.SZ", "600000.SH"]

    async def fake_sync_kline(
        session,
        ts_codes,
        start_date,
        end_date,
        providers=None,
        progress_callback=None,
        commit_each=False,
        concurrency=1,
    ):
        captured["sync"] = {
            "session": session,
            "ts_codes": ts_codes,
            "start_date": start_date,
            "end_date": end_date,
            "commit_each": commit_each,
            "concurrency": concurrency,
        }
        progress_callback(1, len(ts_codes), ts_codes[0])
        progress_callback(2, len(ts_codes), ts_codes[1])
        return {
            "requested_symbols": len(ts_codes),
            "inserted_or_updated": 2,
            "source_counts": {"eastmoney": 2},
            "failures": [],
        }

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "select_all_stock_codes", fake_select_all_stock_codes)
    monkeypatch.setattr(data_tasks, "sync_kline", fake_sync_kline)
    monkeypatch.setattr(data_tasks.sync_all_kline, "update_state", lambda **kwargs: updates.append(kwargs))

    result = data_tasks.sync_all_kline.run(start_date="2026-05-01", end_date="2026-05-02")

    assert result["requested_symbols"] == 2
    assert captured["tracked"]["payload"]["concurrency"] == 2
    assert captured["sync"]["commit_each"] is True
    assert captured["sync"]["concurrency"] == 2
    assert updates == [
        {"state": "PROGRESS", "meta": {"current": 1, "total": 2, "current_code": "000001.SZ"}},
        {"state": "PROGRESS", "meta": {"current": 2, "total": 2, "current_code": "600000.SH"}},
    ]


def test_sync_all_kline_task_honors_explicit_concurrency(monkeypatch) -> None:
    from app.tasks import data_tasks

    class FakeSession:
        async def close(self):
            return None

    fake_session = FakeSession()
    captured = {}

    async def fake_run_tracked(task_name, task_id, payload, fn):
        captured["payload"] = payload
        return await fn(_FakeFactory(fake_session))

    async def fake_select_all_stock_codes(session):
        return ["000001.SZ"]

    async def fake_sync_kline(
        session,
        ts_codes,
        start_date,
        end_date,
        providers=None,
        progress_callback=None,
        commit_each=False,
        concurrency=1,
    ):
        captured["concurrency"] = concurrency
        return {"requested_symbols": 1, "inserted_or_updated": 1, "source_counts": {}, "failures": []}

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "select_all_stock_codes", fake_select_all_stock_codes)
    monkeypatch.setattr(data_tasks, "sync_kline", fake_sync_kline)

    result = data_tasks.sync_all_kline.run(start_date="2026-05-01", end_date="2026-05-02", concurrency=5)

    assert result["requested_symbols"] == 1
    assert captured["payload"]["concurrency"] == 5
    assert captured["concurrency"] == 5


def test_run_tracked_claims_pending_task_run(monkeypatch) -> None:
    from app.tasks import tracking

    class FakeScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.params = []
            self.commits = 0

        async def execute(self, statement, params=None):
            self.statements.append(str(statement))
            self.params.append(params or {})
            if "UPDATE task_runs" in str(statement) and "RETURNING id" in str(statement):
                return FakeScalarResult(7)
            return FakeScalarResult(None)

        async def commit(self):
            self.commits += 1

    class FakeFactory:
        def __init__(self):
            self.session = FakeSession()

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    factory = FakeFactory()
    monkeypatch.setattr(tracking, "async_session_factory", lambda: factory)

    async def run():
        return await _run_tracked("sync_sample_kline", "task-123", {}, lambda _session: _success())

    async def _success():
        return {"ok": True}

    result = asyncio.run(run())

    assert result == {"ok": True}
    assert "UPDATE task_runs" in factory.session.statements[0]
    assert factory.session.params[0]["task_id"] == "task-123"
    assert factory.session.params[-1]["status"] == "success"


def test_run_tracked_rolls_back_before_recording_failed_status(monkeypatch) -> None:
    from app.tasks import tracking

    class FakeScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.params = []
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, statement, params=None):
            sql = str(statement)
            self.statements.append(sql)
            self.params.append(params or {})
            if "UPDATE task_runs" in sql and "RETURNING id" in sql:
                return FakeScalarResult(11)
            if "SELECT broken_business_sql" in sql:
                raise RuntimeError("business SQL failed")
            return FakeScalarResult(None)

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeFactory:
        def __init__(self):
            self.session = FakeSession()

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    factory = FakeFactory()
    monkeypatch.setattr(tracking, "async_session_factory", lambda: factory)

    async def run():
        async def fail(session_factory):
            async with session_factory() as session:
                await session.execute(text("SELECT broken_business_sql"))
            return {"ok": True}

        return await _run_tracked("compute_daily_factors", "task-failed", {}, fail)

    with pytest.raises(RuntimeError, match="business SQL failed"):
        asyncio.run(run())

    assert factory.session.rollbacks == 1
    assert factory.session.params[-1]["status"] == "failed"
    assert factory.session.params[-1]["error_message"] == "business SQL failed"


def test_snapshot_nav_daily_skips_non_trading_day() -> None:
    class FakeResult:
        def __init__(self, rows=None):
            self._rows = rows or []

        def mappings(self):
            return self

        def all(self):
            return self._rows

        def one_or_none(self):
            return self._rows[0] if self._rows else None

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.params = []

        async def execute(self, statement, params=None):
            self.statements.append(str(statement))
            self.params.append(params or {})
            return FakeResult([{"is_open": False}])

    session = FakeSession()
    result = asyncio.run(_snapshot_nav_daily(session, date(2026, 5, 23)))

    assert result == {"nav_date": "2026-05-23", "skipped": True, "reason": "non-trading day"}
    assert "FROM trade_calendar" in session.statements[0]
    assert len(session.statements) == 1


def test_snapshot_nav_daily_treats_missing_calendar_as_non_trading_day() -> None:
    class FakeResult:
        def mappings(self):
            return self

        def one_or_none(self):
            return None

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement, params=None):
            self.statements.append(str(statement))
            return FakeResult()

    session = FakeSession()
    result = asyncio.run(_snapshot_nav_daily(session, date(2026, 5, 23)))

    assert result["skipped"] is True
    assert result["reason"] == "non-trading day"


def test_realtime_trading_time_only_allows_a_share_session() -> None:
    assert _is_realtime_trading_time(datetime(2026, 5, 21, 9, 30)) is True
    assert _is_realtime_trading_time(datetime(2026, 5, 21, 11, 45)) is False
    assert _is_realtime_trading_time(datetime(2026, 5, 21, 14, 59)) is True
    assert _is_realtime_trading_time(datetime(2026, 5, 21, 15, 30)) is False


def test_open_trade_day_helper_treats_missing_calendar_as_closed() -> None:
    class FakeResult:
        def mappings(self):
            return self

        def one_or_none(self):
            return None

    class FakeSession:
        async def execute(self, statement, params=None):
            return FakeResult()

    assert asyncio.run(_is_open_trade_day(FakeSession(), date(2026, 5, 23))) is False
