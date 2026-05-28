from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def one(self):
        return self._rows[0]

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar_one(self):
        return self._rows[0]


class FakeTaskSession:
    def __init__(self):
        self.statements = []
        self.params = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        return FakeResult([1])

    async def commit(self):
        self.commits += 1


class FakeFullKlineSession(FakeTaskSession):
    def __init__(self, active_task=None):
        super().__init__()
        self.active_task = active_task

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(params or {})
        if "UPDATE task_runs" in sql and "RETURNING id" in sql:
            return FakeResult([])
        if "FROM task_runs" in sql and "status IN ('pending', 'running')" in sql:
            return FakeResult([self.active_task] if self.active_task else [])
        if "FROM user_preferences" in sql:
            return FakeResult([])
        return FakeResult([1])


class FakeSession:
    def __init__(self):
        self.calls = 0

    async def execute(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return FakeResult(
                [
                    {
                        "stock_basic_count": 0,
                        "trade_calendar_count": 0,
                        "latest_trade_calendar_date": None,
                        "daily_kline_count": 0,
                        "latest_kline_trade_date": None,
                    }
                ]
            )
        return FakeResult([])


class FakeCeleryInspector:
    def __init__(self, task_ids=None):
        self.task_ids = set(task_ids or [])

    def active(self):
        return {"worker-a": [{"id": task_id} for task_id in self.task_ids]}

    def reserved(self):
        return {}

    def scheduled(self):
        return {}


def test_data_status_returns_stable_empty_shape() -> None:
    async def override_session():
        yield FakeSession()

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/data/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["stock_basic_count"] == 0
    assert body["trade_calendar_count"] == 0
    assert body["daily_kline_count"] == 0
    assert body["recent_tasks"] == []
    assert body["recent_alerts"] == []


def test_sample_kline_task_reports_queue_unavailable(monkeypatch) -> None:
    from kombu.exceptions import OperationalError

    from app.api import tasks as task_api

    fake_session = FakeTaskSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "get_full_kline_sync_concurrency", AsyncMock(return_value=2))
    monkeypatch.setattr(task_api.sync_sample_kline, "apply_async", Mock(side_effect=OperationalError("redis down")))
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sample-kline", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "task queue unavailable" in response.json()["detail"]
    assert "INSERT INTO task_runs" in fake_session.statements[0]
    assert "UPDATE task_runs" in fake_session.statements[1]


def test_sample_kline_task_writes_pending_task_run(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeTaskSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "get_full_kline_sync_concurrency", AsyncMock(return_value=2))
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "task-123"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_sample_kline, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sample-kline", json={"ts_codes": ["000001.SZ"]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-123", "status": "pending"}
    assert "INSERT INTO task_runs" in fake_session.statements[0]
    assert fake_session.params[0]["task_name"] == "sync_sample_kline"
    assert fake_session.params[0]["task_id"] == "task-123"
    apply_async.assert_called_once_with(
        kwargs={"ts_codes": ["000001.SZ"], "start_date": None, "end_date": None, "concurrency": 2},
        task_id="task-123",
    )


def test_sample_kline_task_accepts_requested_concurrency(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeTaskSession()

    async def override_session():
        yield fake_session

    get_full_kline_sync_concurrency = AsyncMock(return_value=7)
    monkeypatch.setattr(task_api, "get_full_kline_sync_concurrency", get_full_kline_sync_concurrency)
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "task-sample"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_sample_kline, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sample-kline", json={"concurrency": 3})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    get_full_kline_sync_concurrency.assert_not_called()
    apply_async.assert_called_once_with(
        kwargs={"ts_codes": None, "start_date": None, "end_date": None, "concurrency": 3},
        task_id="task-sample",
    )


def test_sample_kline_rejects_invalid_concurrency() -> None:
    client = TestClient(app)
    response = client.post("/api/tasks/data/sample-kline", json={"concurrency": 0})

    assert response.status_code == 422


def test_sync_all_kline_rejects_without_active_worker(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: [])
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "no active celery worker" in response.json()["detail"]
    assert not any("INSERT INTO task_runs" in statement for statement in fake_session.statements)


def test_sync_all_kline_rejects_multiple_workers(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a", "worker-b"])
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "multiple celery workers detected" in response.json()["detail"]
    assert not any("INSERT INTO task_runs" in statement for statement in fake_session.statements)


def test_sync_all_kline_rejects_existing_active_task(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession(
        {
            "id": 1,
            "task_name": "sync_all_kline",
            "task_id": "existing-task",
            "status": "running",
            "started_at": None,
            "payload": {},
        }
    )

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector(["existing-task"]))
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "existing-task" in response.json()["detail"]
    assert not any("INSERT INTO task_runs" in statement for statement in fake_session.statements)


def test_sync_all_kline_marks_orphaned_active_task_failed(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession(
        {
            "id": 1,
            "task_name": "sync_all_kline",
            "task_id": "orphaned-task",
            "status": "running",
            "started_at": None,
            "payload": {},
        }
    )

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "all-task-123"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_all_kline, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_session.params[2]["task_id"] == "orphaned-task"
    assert fake_session.params[2]["statuses"] == ["pending", "running"]
    assert "orphaned full kline sync" in fake_session.params[2]["error_message"]
    assert any("INSERT INTO task_runs" in statement for statement in fake_session.statements)
    apply_async.assert_called_once_with(
        kwargs={"start_date": None, "end_date": None, "concurrency": 2},
        task_id="all-task-123",
    )


def test_sync_all_kline_writes_pending_after_preflight(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "all-task-123"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_all_kline, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"task_id": "all-task-123", "status": "pending"}
    assert any("INSERT INTO task_runs" in statement for statement in fake_session.statements)
    apply_async.assert_called_once_with(
        kwargs={"start_date": None, "end_date": None, "concurrency": 2},
        task_id="all-task-123",
    )


def test_sync_all_kline_uses_saved_concurrency_when_request_omits_it(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "all-task-456"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_all_kline, "apply_async", apply_async)
    monkeypatch.setattr(task_api, "get_full_kline_sync_concurrency", AsyncMock(return_value=4))
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    apply_async.assert_called_once_with(
        kwargs={"start_date": None, "end_date": None, "concurrency": 4},
        task_id="all-task-456",
    )


def test_sync_all_kline_applies_requested_concurrency(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    get_full_kline_sync_concurrency = AsyncMock(return_value=7)
    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    monkeypatch.setattr(task_api, "get_full_kline_sync_concurrency", get_full_kline_sync_concurrency)
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "all-task-789"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_all_kline, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline", json={"concurrency": 5})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    get_full_kline_sync_concurrency.assert_not_called()
    apply_async.assert_called_once_with(
        kwargs={"start_date": None, "end_date": None, "concurrency": 5},
        task_id="all-task-789",
    )


def test_sync_all_kline_rejects_invalid_concurrency(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/sync-all-kline", json={"concurrency": 9})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_fundamentals_rejects_without_active_worker(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: [])
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "no active celery worker" in response.json()["detail"]
    assert not any("INSERT INTO task_runs" in statement for statement in fake_session.statements)


def test_fundamentals_rejects_multiple_workers(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a", "worker-b"])
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "multiple celery workers detected" in response.json()["detail"]
    assert not any("INSERT INTO task_runs" in statement for statement in fake_session.statements)


def test_fundamentals_rejects_existing_active_task(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession(
        {
            "id": 1,
            "task_name": "sync_fundamentals",
            "task_id": "existing-fundamentals-task",
            "status": "running",
            "started_at": None,
            "payload": {},
        }
    )

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector(["existing-fundamentals-task"]))
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "existing-fundamentals-task" in response.json()["detail"]
    assert not any("INSERT INTO task_runs" in statement for statement in fake_session.statements)


def test_fundamentals_marks_orphaned_active_task_failed(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession(
        {
            "id": 1,
            "task_name": "sync_fundamentals",
            "task_id": "orphaned-fundamentals-task",
            "status": "running",
            "started_at": None,
            "payload": {},
        }
    )

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "fundamentals-task-123"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_fundamentals_task, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_session.params[2]["task_id"] == "orphaned-fundamentals-task"
    assert fake_session.params[2]["statuses"] == ["pending", "running"]
    assert "orphaned fundamentals sync" in fake_session.params[2]["error_message"]
    assert any("INSERT INTO task_runs" in statement for statement in fake_session.statements)
    apply_async.assert_called_once_with(
        kwargs={"ts_codes": None, "start_date": None, "end_date": None, "concurrency": 2},
        task_id="fundamentals-task-123",
    )


def test_fundamentals_writes_pending_after_preflight(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "fundamentals-task-123"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_fundamentals_task, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"task_id": "fundamentals-task-123", "status": "pending"}
    assert any("INSERT INTO task_runs" in statement for statement in fake_session.statements)
    apply_async.assert_called_once_with(
        kwargs={"ts_codes": None, "start_date": None, "end_date": None, "concurrency": 2},
        task_id="fundamentals-task-123",
    )


def test_fundamentals_uses_saved_concurrency_when_request_omits_it(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "fundamentals-task-456"})())
    monkeypatch.setattr(task_api, "get_full_kline_sync_concurrency", AsyncMock(return_value=4))
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_fundamentals_task, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    apply_async.assert_called_once_with(
        kwargs={"ts_codes": None, "start_date": None, "end_date": None, "concurrency": 4},
        task_id="fundamentals-task-456",
    )


def test_fundamentals_applies_requested_concurrency(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    get_full_kline_sync_concurrency = AsyncMock(return_value=7)
    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    monkeypatch.setattr(task_api, "get_full_kline_sync_concurrency", get_full_kline_sync_concurrency)
    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "fundamentals-task-789"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.sync_fundamentals_task, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={"concurrency": 5})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    get_full_kline_sync_concurrency.assert_not_called()
    apply_async.assert_called_once_with(
        kwargs={"ts_codes": None, "start_date": None, "end_date": None, "concurrency": 5},
        task_id="fundamentals-task-789",
    )


def test_fundamentals_rejects_invalid_concurrency(monkeypatch) -> None:
    from app.api import tasks as task_api

    fake_session = FakeFullKlineSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "_active_celery_worker_names", lambda: ["worker-a"])
    monkeypatch.setattr(task_api, "_celery_inspector", lambda: FakeCeleryInspector())
    app.dependency_overrides[get_session] = override_session

    try:
        client = TestClient(app)
        response = client.post("/api/tasks/data/fundamentals", json={"concurrency": 9})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_task_status_serializes_revoked_result(monkeypatch) -> None:
    from celery.exceptions import TaskRevokedError

    from app.api import tasks as task_api

    class RevokedAsyncResult:
        status = "REVOKED"
        info = TaskRevokedError("terminated")
        result = TaskRevokedError("terminated")

        def __init__(self, task_id, app):
            self.task_id = task_id
            self.app = app

        def ready(self):
            return True

        def failed(self):
            return False

    monkeypatch.setattr(task_api, "AsyncResult", RevokedAsyncResult)

    client = TestClient(app)
    response = client.get("/api/tasks/task-revoked")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-revoked",
        "status": "revoked",
        "ready": True,
        "error": "terminated",
    }


def test_sync_kline_result_serializes_revoked_result(monkeypatch) -> None:
    from celery.exceptions import TaskRevokedError

    from app.api import data as data_api

    class RevokedAsyncResult:
        status = "REVOKED"
        result = TaskRevokedError("terminated")

        def __init__(self, task_id, app):
            self.task_id = task_id
            self.app = app

        def ready(self):
            return True

        def failed(self):
            return False

    monkeypatch.setattr(data_api, "AsyncResult", RevokedAsyncResult)

    client = TestClient(app)
    response = client.get("/api/data/sync/kline/result/task-revoked")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-revoked",
        "status": "revoked",
        "ready": True,
        "error": "terminated",
    }


def test_source_check_endpoint_returns_probe_result(monkeypatch) -> None:
    from app.api import sources as sources_api

    async def fake_check_source(name):
        return {
            "name": name,
            "ok": True,
            "checked_capability": "daily_kline",
            "records": 1,
            "latency_ms": 12,
            "checked_at": "2026-05-24T00:00:00+00:00",
            "error": None,
        }

    monkeypatch.setattr(sources_api, "check_source", fake_check_source)

    client = TestClient(app)
    response = client.post("/api/data/sources/eastmoney_http/check")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["checked_capability"] == "daily_kline"


def test_source_check_endpoint_rejects_unknown_source() -> None:
    client = TestClient(app)
    response = client.post("/api/data/sources/not_real/check")

    assert response.status_code == 400
    assert "unknown source name" in response.json()["detail"]
