"""Tests for the K-line sync endpoints in ``app.api.tasks``.

Covers the rebuilt DB-queue architecture: ``kline_sync_dispatch`` (creates
``kline_sync_jobs`` + items + starts workers) and ``kline_sync_worker``
(processes items from the DB queue). The retry endpoints reset
permanently_failed items and dispatch a fresh worker.
"""
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one(self):
        return self._rows[0]

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeTaskSession:
    """Session stub for retry endpoints (SELECT job + reset items + commit)."""

    def __init__(self, *, latest_job_id=None):
        self.statements = []
        self.params = []
        self.commits = 0
        self._latest_job_id = latest_job_id

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        sql = str(statement)
        if "SELECT id FROM kline_sync_jobs" in sql:
            if self._latest_job_id is not None:
                return FakeResult([(self._latest_job_id,)])
            return FakeResult([])
        return FakeResult([1])

    async def commit(self):
        self.commits += 1


def _override_session(fake_session):
    async def override():
        yield fake_session

    return override


# ---------------------------------------------------------------------------
# POST /api/tasks/data/incremental-kline
# ---------------------------------------------------------------------------


def test_incremental_kline_dispatches_kline_sync_dispatch(monkeypatch) -> None:
    from app.api import tasks as task_api

    monkeypatch.setattr(task_api, "_guard_beat_lock_free", AsyncMock())
    apply_async = Mock(return_value=Mock(id="task-abc"))
    monkeypatch.setattr(task_api.kline_sync_dispatch, "apply_async", apply_async)

    client = TestClient(app)
    response = client.post("/api/tasks/data/incremental-kline", json={"ts_codes": ["000001.SZ"]})

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-abc", "status": "dispatched"}
    apply_async.assert_called_once_with(
        kwargs={"job_type": "incremental", "ts_codes": ["000001.SZ"]},
    )


def test_incremental_kline_dispatches_without_ts_codes(monkeypatch) -> None:
    from app.api import tasks as task_api

    monkeypatch.setattr(task_api, "_guard_beat_lock_free", AsyncMock())
    apply_async = Mock(return_value=Mock(id="task-def"))
    monkeypatch.setattr(task_api.kline_sync_dispatch, "apply_async", apply_async)

    client = TestClient(app)
    response = client.post("/api/tasks/data/incremental-kline", json={})

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-def", "status": "dispatched"}
    apply_async.assert_called_once_with(kwargs={"job_type": "incremental"})


# ---------------------------------------------------------------------------
# POST /api/tasks/data/sync-all-kline
# ---------------------------------------------------------------------------


def test_sync_all_kline_dispatches_kline_sync_dispatch_full(monkeypatch) -> None:
    from app.api import tasks as task_api

    monkeypatch.setattr(task_api, "_guard_beat_lock_free", AsyncMock())
    apply_async = Mock(return_value=Mock(id="task-full"))
    monkeypatch.setattr(task_api.kline_sync_dispatch, "apply_async", apply_async)

    client = TestClient(app)
    response = client.post("/api/tasks/data/sync-all-kline", json={})

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-full", "status": "dispatched"}
    apply_async.assert_called_once_with(
        kwargs={"job_type": "full", "start_date": None, "end_date": None},
    )


def test_sync_all_kline_passes_date_overrides(monkeypatch) -> None:
    from app.api import tasks as task_api

    monkeypatch.setattr(task_api, "_guard_beat_lock_free", AsyncMock())
    apply_async = Mock(return_value=Mock(id="task-full-dates"))
    monkeypatch.setattr(task_api.kline_sync_dispatch, "apply_async", apply_async)

    client = TestClient(app)
    response = client.post(
        "/api/tasks/data/sync-all-kline",
        json={"start_date": "2025-01-01", "end_date": "2025-06-30"},
    )

    assert response.status_code == 200
    apply_async.assert_called_once_with(
        kwargs={"job_type": "full", "start_date": "2025-01-01", "end_date": "2025-06-30"},
    )

