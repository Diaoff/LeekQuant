import asyncio
from datetime import date

import pytest
from sqlalchemy import text

from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked
from app.tasks.trading_tasks import _snapshot_nav_daily


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
        async def fail(session):
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
