from unittest.mock import Mock

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
        kwargs={"ts_codes": ["000001.SZ"], "start_date": None, "end_date": None},
        task_id="task-123",
    )
