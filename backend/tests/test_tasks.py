import asyncio
from datetime import date, datetime

import pytest
from sqlalchemy import text

from app.tasks.beat_lock import BeatLock
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked
from app.tasks.trading_tasks import _is_realtime_trading_time, _is_open_trade_day, _snapshot_nav_daily


@pytest.fixture(autouse=True)
def _neutralize_beat_lock(monkeypatch):
    """Disable the real Redis beat lock so unit tests can call task.run() directly.

    In a running deployment a real celery beat worker may hold the
    ``beat:lock:`` key for a task, which makes ``with_beat_lock`` short-circuit
    and return None. Unit tests invoke the task body directly and must not be
    blocked by that external lock state.
    """
    monkeypatch.setattr(BeatLock, "acquire", lambda self, task_name: True)
    monkeypatch.setattr(BeatLock, "release", lambda self, task_name: None)


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


class _FakeSessionCtx:
    """Async context manager yielding a fake session."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class _FakeSessionFactory:
    """Factory that returns a new context manager each call, all wrapping the same session."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return _FakeSessionCtx(self._session)


class _FakeTime:
    """Replaces the time module in data_tasks for controlled monotonic() values."""

    def __init__(self, values):
        self._iter = iter(values)

    def monotonic(self):
        return next(self._iter)


def test_celery_app_registers_data_tasks() -> None:
    celery_app.loader.import_default_modules()

    registered = set(celery_app.tasks)

    assert "app.tasks.data_tasks.update_stock_basic" in registered
    assert "app.tasks.data_tasks.update_trade_calendar" in registered
    assert "app.tasks.data_tasks.sync_sample_kline" in registered
    assert "app.tasks.data_tasks.kline_sync_dispatch" in registered
    assert "app.tasks.data_tasks.kline_sync_worker" in registered
    assert "app.tasks.data_tasks.kline_sync_recover_stuck" in registered
    assert "app.tasks.trading_tasks.unlock_t1_daily" in registered
    assert "app.tasks.trading_tasks.match_pending_orders" in registered
    assert "app.tasks.trading_tasks.snapshot_nav_daily" in registered
    assert "app.tasks.signal_tasks.generate_all_signals" in registered
    assert celery_app.conf.beat_schedule["generate-signals-daily"]["task"] == "app.tasks.signal_tasks.generate_all_signals"
    assert celery_app.conf.beat_schedule["incremental-kline-daily"]["task"] == "app.tasks.data_tasks.kline_sync_dispatch"
    assert celery_app.conf.beat_schedule["kline-sync-recover-stuck"]["task"] == "app.tasks.data_tasks.kline_sync_recover_stuck"
    assert "reconcile-kline-batches" not in celery_app.conf.beat_schedule
    assert celery_app.conf.worker_prefetch_multiplier == 1


# ---------------------------------------------------------------------------
# K-line sync — DB queue architecture tests
# ---------------------------------------------------------------------------


def test_kline_sync_dispatch_creates_job_and_items(monkeypatch) -> None:
    """Dispatch creates a kline_sync_jobs row, inserts items, starts workers."""
    from app.tasks import data_tasks

    class _FakeSession:
        async def close(self):
            return None

    fake_session = _FakeSession()
    captured = {}
    dispatched_workers = []
    updates = []

    async def fake_run_tracked(task_name, task_id, payload, fn):
        captured["task_name"] = task_name
        captured["payload"] = payload
        return await fn(_FakeFactory(fake_session, close_on_exit=False))

    async def fake_create_kline_sync_job(session, *, job_type, config):
        captured["job_type"] = job_type
        captured["config"] = config
        return 42

    async def fake_infer_incremental_kline_ranges(session, *, ts_codes=None, limit=None):
        return [
            {"ts_code": "000001.SZ", "start_date": date(2026, 5, 1), "end_date": date(2026, 5, 2), "last_trade_date": date(2026, 4, 30)},
            {"ts_code": "600000.SH", "start_date": date(2026, 5, 1), "end_date": date(2026, 5, 2), "last_trade_date": date(2026, 4, 30)},
        ]

    def fake_split(ranges):
        return ranges

    async def fake_insert_kline_sync_items(session, *, job_id, items):
        captured["insert_job_id"] = job_id
        captured["insert_items"] = items
        return len(items)

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "create_kline_sync_job", fake_create_kline_sync_job)
    monkeypatch.setattr(data_tasks, "infer_incremental_kline_ranges", fake_infer_incremental_kline_ranges)
    monkeypatch.setattr(data_tasks, "split_kline_ranges_by_year", fake_split)
    monkeypatch.setattr(data_tasks, "insert_kline_sync_items", fake_insert_kline_sync_items)
    monkeypatch.setattr(data_tasks.kline_sync_worker, "apply_async", lambda **kw: dispatched_workers.append(kw))
    monkeypatch.setattr(data_tasks.kline_sync_dispatch, "update_state", lambda **kw: updates.append(kw))

    result = data_tasks.kline_sync_dispatch.run()

    assert captured["task_name"] == "kline_sync_dispatch"
    assert captured["job_type"] == "incremental"
    assert captured["insert_job_id"] == 42
    assert len(captured["insert_items"]) == 2
    assert captured["insert_items"][0]["ts_code"] == "000001.SZ"
    assert captured["insert_items"][1]["ts_code"] == "600000.SH"
    assert result["job_id"] == 42
    assert result["scope_total"] == 2
    assert result["_task_status"] == "dispatched"
    assert len(dispatched_workers) == data_tasks.get_settings().kline_sync_worker_count
    assert all(w["kwargs"] == {"job_id": 42} for w in dispatched_workers)
    assert updates[-1]["state"] == "PROGRESS"
    assert updates[-1]["meta"]["total"] == 2
    assert updates[-1]["meta"]["pending"] == 2


def test_kline_sync_dispatch_skips_when_no_gaps(monkeypatch) -> None:
    """When no gaps are found, dispatch returns skipped=True and starts no workers."""
    from app.tasks import data_tasks

    class _FakeSession:
        async def close(self):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    dispatched_workers = []

    async def fake_run_tracked(task_name, task_id, payload, fn):
        return await fn(_FakeFactory(_FakeSession(), close_on_exit=False))

    async def fake_create_kline_sync_job(session, *, job_type, config):
        return 99

    async def fake_infer_incremental_kline_ranges(session, *, ts_codes=None, limit=None):
        return []

    def fake_split(ranges):
        return ranges

    async def fake_insert(session, *, job_id, items):
        return 0

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "create_kline_sync_job", fake_create_kline_sync_job)
    monkeypatch.setattr(data_tasks, "infer_incremental_kline_ranges", fake_infer_incremental_kline_ranges)
    monkeypatch.setattr(data_tasks, "split_kline_ranges_by_year", fake_split)
    monkeypatch.setattr(data_tasks, "insert_kline_sync_items", fake_insert)
    monkeypatch.setattr(data_tasks.kline_sync_worker, "apply_async", lambda **kw: dispatched_workers.append(kw))

    result = data_tasks.kline_sync_dispatch.run()

    assert result["skipped"] is True
    assert result["scope_total"] == 0
    assert result["workers"] == 0
    assert len(dispatched_workers) == 0


def test_kline_sync_worker_processes_items(monkeypatch) -> None:
    """Worker claims items, calls sync_one_stock, marks done/failed."""
    from app.tasks import data_tasks

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            pass

        async def commit(self):
            pass

    fake_session = _FakeSession()
    claim_calls = []
    mark_done_calls = []
    mark_failed_calls = []
    sync_calls = []
    create_alert_calls = []

    # First claim returns 2 items; second claim returns empty → worker exits loop.
    claim_returns = [
        [
            {"id": 1, "ts_code": "000001.SZ", "start_date": date(2026, 5, 1), "end_date": date(2026, 5, 2)},
            {"id": 2, "ts_code": "600000.SH", "start_date": date(2026, 5, 1), "end_date": date(2026, 5, 2)},
        ],
        [],
    ]

    async def fake_claim(session, *, job_id, count, worker_id):
        claim_calls.append({"job_id": job_id, "count": count, "worker_id": worker_id})
        return claim_returns.pop(0) if claim_returns else []

    async def fake_sync_one_stock(sf, ts_code, start_date, end_date):
        sync_calls.append({"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
        if ts_code == "000001.SZ":
            return {"success": True, "error": None, "source": "adata", "synced": 10}
        return {"success": False, "error": "connection timeout", "source": None, "synced": 0}

    async def fake_mark_done(session, *, item_id, job_id):
        mark_done_calls.append({"item_id": item_id, "job_id": job_id})

    async def fake_mark_failed(session, *, item_id, job_id, error, max_attempts):
        mark_failed_calls.append({"item_id": item_id, "job_id": job_id, "error": error, "max_attempts": max_attempts})
        return False  # not permanent

    async def fake_complete(session, *, job_id):
        return True

    async def fake_create_alert(session, **kwargs):
        create_alert_calls.append(kwargs)

    monkeypatch.setattr(data_tasks, "async_session_factory", _FakeSessionFactory(fake_session))
    monkeypatch.setattr(data_tasks, "claim_kline_sync_items", fake_claim)
    monkeypatch.setattr(data_tasks, "sync_one_stock", fake_sync_one_stock)
    monkeypatch.setattr(data_tasks, "mark_item_done", fake_mark_done)
    monkeypatch.setattr(data_tasks, "mark_item_failed", fake_mark_failed)
    monkeypatch.setattr(data_tasks, "complete_job_if_done", fake_complete)
    monkeypatch.setattr(data_tasks, "create_alert", fake_create_alert)

    result = data_tasks.kline_sync_worker.run(job_id=42)

    # Worker claimed 2 items
    assert len(claim_calls) == 2  # first claim (2 items) + second claim (empty)
    assert claim_calls[0]["job_id"] == 42
    assert claim_calls[0]["count"] == data_tasks.get_settings().kline_sync_worker_concurrency

    # sync_one_stock was called for both items
    assert len(sync_calls) == 2
    assert sync_calls[0]["ts_code"] == "000001.SZ"
    assert sync_calls[1]["ts_code"] == "600000.SH"

    # Successful item → mark_item_done; failed item → mark_item_failed
    assert len(mark_done_calls) == 1
    assert mark_done_calls[0]["item_id"] == 1
    assert len(mark_failed_calls) == 1
    assert mark_failed_calls[0]["item_id"] == 2
    assert mark_failed_calls[0]["error"] == "connection timeout"

    # Job completed
    assert result["completed"] is True
    assert result["processed"] == 2
    # No permanent failure → no alert
    assert create_alert_calls == []


def test_kline_sync_worker_self_requeue(monkeypatch) -> None:
    """When budget expires with pending items remaining, controller handles re-launch."""
    from app.tasks import data_tasks

    settings = data_tasks.get_settings()
    budget = settings.kline_sync_worker_budget_seconds

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            pass

        async def commit(self):
            pass

    fake_session = _FakeSession()
    requeue_calls = []

    # Simulate: start=0, first loop check=0 (enter), second check=budget+1 (exit)
    time_values = [0.0, 0.0, float(budget + 1)]
    monkeypatch.setattr(data_tasks, "time", _FakeTime(time_values))

    async def fake_claim(session, *, job_id, count, worker_id):
        return [{"id": 1, "ts_code": "000001.SZ", "start_date": date(2026, 5, 1), "end_date": date(2026, 5, 2)}]

    async def fake_sync_one_stock(sf, ts_code, start_date, end_date):
        return {"success": True, "error": None, "source": "adata", "synced": 5}

    async def fake_mark_done(session, *, item_id, job_id):
        pass

    async def fake_complete(session, *, job_id):
        return False  # job not done

    async def fake_get_progress(session, *, job_id):
        return {
            "scope_total": 100,
            "scope_done": 1,
            "scope_failed": 0,
            "permanent_failure_codes": [],
            "pending": 99,
            "running": 0,
            "done": 1,
            "permanently_failed": 0,
            "status": "running",
        }

    monkeypatch.setattr(data_tasks, "async_session_factory", _FakeSessionFactory(fake_session))
    monkeypatch.setattr(data_tasks, "claim_kline_sync_items", fake_claim)
    monkeypatch.setattr(data_tasks, "sync_one_stock", fake_sync_one_stock)
    monkeypatch.setattr(data_tasks, "mark_item_done", fake_mark_done)
    monkeypatch.setattr(data_tasks, "complete_job_if_done", fake_complete)
    monkeypatch.setattr(data_tasks, "get_job_progress", fake_get_progress)
    monkeypatch.setattr(data_tasks.kline_sync_worker, "apply_async", lambda **kw: requeue_calls.append(kw))

    result = data_tasks.kline_sync_worker.run(job_id=42)

    # Worker processed 1 item then budget expired
    assert result["processed"] == 1
    assert result["completed"] is False
    # Worker does NOT self-requeue — controller (recover_stuck) handles re-launch
    assert len(requeue_calls) == 0


def test_kline_sync_worker_no_requeue_when_queue_empty(monkeypatch) -> None:
    """When queue is empty and job is complete, worker does NOT self-requeue."""
    from app.tasks import data_tasks

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            pass

        async def commit(self):
            pass

    fake_session = _FakeSession()
    requeue_calls = []

    async def fake_claim(session, *, job_id, count, worker_id):
        return []  # queue empty

    async def fake_complete(session, *, job_id):
        return True  # job done

    monkeypatch.setattr(data_tasks, "async_session_factory", _FakeSessionFactory(fake_session))
    monkeypatch.setattr(data_tasks, "claim_kline_sync_items", fake_claim)
    monkeypatch.setattr(data_tasks, "sync_one_stock", lambda *a, **kw: None)
    monkeypatch.setattr(data_tasks, "complete_job_if_done", fake_complete)
    monkeypatch.setattr(data_tasks.kline_sync_worker, "apply_async", lambda **kw: requeue_calls.append(kw))

    result = data_tasks.kline_sync_worker.run(job_id=42)

    assert result["processed"] == 0
    assert result["completed"] is True
    assert requeue_calls == []


def test_kline_sync_recover_stuck(monkeypatch) -> None:
    """Recover stuck task calls recover_stuck_items, re-launches workers, and returns counts."""
    from app.tasks import data_tasks

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            return self

        def all(self):
            return []  # no pending jobs to re-launch

        async def commit(self):
            pass

    fake_session = _FakeSession()
    recover_calls = []
    launch_calls = []

    async def fake_recover(session, *, stuck_seconds):
        recover_calls.append(stuck_seconds)
        return 7

    def fake_apply_async(**kw):
        launch_calls.append(kw)

    monkeypatch.setattr(data_tasks, "async_session_factory", _FakeSessionFactory(fake_session))
    monkeypatch.setattr(data_tasks, "recover_stuck_items", fake_recover)
    monkeypatch.setattr(data_tasks.kline_sync_worker, "apply_async", fake_apply_async)

    result = data_tasks.kline_sync_recover_stuck.run()

    assert recover_calls == [data_tasks.get_settings().kline_sync_stuck_seconds]
    assert result["recovered"] == 7
    assert result["launched"] == 0
    assert result["stuck_seconds"] == data_tasks.get_settings().kline_sync_stuck_seconds


# ---------------------------------------------------------------------------
# Non-K-line task tests (kept from original)
# ---------------------------------------------------------------------------


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
    assert captured["tracked"]["payload"]["concurrency"] == 8
    assert captured["select_session"] is fake_session
    assert fake_session.closed is True
    assert captured["sync"]["session"] is None
    assert captured["sync"]["ts_codes"] == ["000001.SZ", "600000.SH"]
    assert captured["sync"]["commit_each"] is True
    assert captured["sync"]["concurrency"] == 8
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
        from_listing=False,
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


def test_sync_sample_kline_task_from_listing_defaults_start_to_2015(monkeypatch) -> None:
    from app.tasks import data_tasks

    captured = {}

    class _NullSession:
        async def close(self):
            return None

    async def fake_run_tracked(task_name, task_id, payload, fn):
        captured["payload"] = payload
        return await fn(_FakeFactory(_NullSession()))

    async def fake_sync_kline(session, ts_codes, start_date, end_date, **kwargs):
        captured["sync"] = {
            "start_date": start_date,
            "end_date": end_date,
            "from_listing": kwargs.get("from_listing"),
        }
        return {"requested_symbols": 1, "inserted_or_updated": 1, "source_counts": {}, "failures": []}

    monkeypatch.setattr(data_tasks, "_run_tracked", fake_run_tracked)
    monkeypatch.setattr(data_tasks, "sync_kline", fake_sync_kline)

    result = data_tasks.sync_sample_kline.run(
        ts_codes=["000001.SZ"],
        from_listing=True,
    )

    assert result["requested_symbols"] == 1
    assert captured["payload"]["from_listing"] is True
    assert captured["payload"]["start_date"] == date(2015, 1, 1)
    assert captured["sync"] == {
        "start_date": date(2015, 1, 1),
        "end_date": captured["payload"]["end_date"],
        "from_listing": True,
    }


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

        return await _run_tracked("kline_sync_dispatch", "task-failed", {}, fail)

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
