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
    apply_async = Mock()
    monkeypatch.setattr(task_api.kline_sync_dispatch, "apply_async", apply_async)

    client = TestClient(app)
    response = client.post("/api/tasks/data/incremental-kline", json={"ts_codes": ["000001.SZ"]})

    assert response.status_code == 200
    assert response.json() == {"status": "dispatched"}
    apply_async.assert_called_once_with(
        kwargs={"job_type": "incremental", "ts_codes": ["000001.SZ"]},
    )


def test_incremental_kline_dispatches_without_ts_codes(monkeypatch) -> None:
    from app.api import tasks as task_api

    monkeypatch.setattr(task_api, "_guard_beat_lock_free", AsyncMock())
    apply_async = Mock()
    monkeypatch.setattr(task_api.kline_sync_dispatch, "apply_async", apply_async)

    client = TestClient(app)
    response = client.post("/api/tasks/data/incremental-kline", json={})

    assert response.status_code == 200
    assert response.json() == {"status": "dispatched"}
    apply_async.assert_called_once_with(kwargs={"job_type": "incremental"})


# ---------------------------------------------------------------------------
# POST /api/tasks/data/sync-all-kline
# ---------------------------------------------------------------------------


def test_sync_all_kline_dispatches_kline_sync_dispatch_full(monkeypatch) -> None:
    from app.api import tasks as task_api

    monkeypatch.setattr(task_api, "_guard_beat_lock_free", AsyncMock())
    apply_async = Mock()
    monkeypatch.setattr(task_api.kline_sync_dispatch, "apply_async", apply_async)

    client = TestClient(app)
    response = client.post("/api/tasks/data/sync-all-kline", json={})

    assert response.status_code == 200
    assert response.json() == {"status": "dispatched"}
    apply_async.assert_called_once_with(
        kwargs={"job_type": "full", "start_date": None, "end_date": None},
    )


def test_sync_all_kline_passes_date_overrides(monkeypatch) -> None:
    from app.api import tasks as task_api

    monkeypatch.setattr(task_api, "_guard_beat_lock_free", AsyncMock())
    apply_async = Mock()
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


# ---------------------------------------------------------------------------
# POST /api/tasks/data/incremental-kline/retry
# ---------------------------------------------------------------------------


def test_retry_incremental_resets_failed_items_and_dispatches_worker(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeTaskSession(latest_job_id=42)
    monkeypatch.setattr(task_api, "reset_failed_items_for_retry", AsyncMock(return_value=5))
    apply_async = Mock()
    monkeypatch.setattr(task_api.kline_sync_worker, "apply_async", apply_async)
    app.dependency_overrides[get_session] = _override_session(fake_session)

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/incremental-kline/retry")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "retrying"
    assert body["reset_count"] == 5
    assert body["job_id"] == 42
    apply_async.assert_called_once_with(kwargs={"job_id": 42})


def test_retry_incremental_returns_noop_when_no_failed_items(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeTaskSession(latest_job_id=7)
    monkeypatch.setattr(task_api, "reset_failed_items_for_retry", AsyncMock(return_value=0))
    apply_async = Mock()
    monkeypatch.setattr(task_api.kline_sync_worker, "apply_async", apply_async)
    app.dependency_overrides[get_session] = _override_session(fake_session)

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/incremental-kline/retry")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "noop"
    assert body["reset_count"] == 0
    assert body["job_id"] == 7
    apply_async.assert_not_called()


def test_retry_incremental_returns_404_when_no_previous_job(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeTaskSession(latest_job_id=None)
    monkeypatch.setattr(task_api, "reset_failed_items_for_retry", AsyncMock(return_value=0))
    app.dependency_overrides[get_session] = _override_session(fake_session)

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/incremental-kline/retry")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "no previous incremental kline sync job" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/tasks/data/sync-all-kline/retry
# ---------------------------------------------------------------------------


def test_retry_full_resets_failed_items_and_dispatches_worker(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeTaskSession(latest_job_id=99)
    monkeypatch.setattr(task_api, "reset_failed_items_for_retry", AsyncMock(return_value=3))
    apply_async = Mock()
    monkeypatch.setattr(task_api.kline_sync_worker, "apply_async", apply_async)
    app.dependency_overrides[get_session] = _override_session(fake_session)

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline/retry")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "retrying"
    assert body["reset_count"] == 3
    assert body["job_id"] == 99
    apply_async.assert_called_once_with(kwargs={"job_id": 99})


def test_retry_full_returns_404_when_no_previous_job(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeTaskSession(latest_job_id=None)
    app.dependency_overrides[get_session] = _override_session(fake_session)

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline/retry")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "no previous full kline sync job" in response.json()["detail"]
