from datetime import date
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app
from app.tasks.celery_app import celery_app


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

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


def test_celery_app_registers_factor_tasks():
    celery_app.loader.import_default_modules()

    registered = set(celery_app.tasks)

    assert "app.tasks.factor_tasks.compute_daily_factors" in registered
    assert "app.tasks.factor_tasks.analyze_factor_icir" in registered
    assert celery_app.conf.beat_schedule["compute-factors-daily"]["task"] == "app.tasks.factor_tasks.compute_daily_factors"


def test_factor_compute_task_endpoint_creates_pending_run(monkeypatch):
    from app.api import tasks as task_api

    fake_session = FakeTaskSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "factor-task-1"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.compute_daily_factors, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        response = TestClient(app).post(
            "/api/tasks/factors/compute",
            json={"trade_date": "2026-05-22", "scope_type": "watchlist_group", "scope_value": "价值"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"task_id": "factor-task-1", "status": "pending"}
    assert fake_session.params[0]["task_name"] == "compute_daily_factors"
    apply_async.assert_called_once_with(
        kwargs={"trade_date": "2026-05-22", "scope_type": "watchlist_group", "scope_value": "价值"},
        task_id="factor-task-1",
    )


def test_factor_compute_task_endpoint_rejects_watchlist_scope_without_group(monkeypatch):
    from app.api import tasks as task_api

    fake_session = FakeTaskSession()
    apply_async = Mock()
    monkeypatch.setattr(task_api.compute_daily_factors, "apply_async", apply_async)

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).post(
            "/api/tasks/factors/compute",
            json={"trade_date": "2026-05-22", "scope_type": "watchlist_group"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert fake_session.statements == []
    apply_async.assert_not_called()


def test_factor_analyze_task_endpoint_creates_pending_run(monkeypatch):
    from app.api import tasks as task_api

    fake_session = FakeTaskSession()

    async def override_session():
        yield fake_session

    monkeypatch.setattr(task_api, "uuid4", lambda: type("FakeUUID", (), {"hex": "factor-task-2"})())
    apply_async = Mock()
    monkeypatch.setattr(task_api.analyze_factor_icir_task, "apply_async", apply_async)
    app.dependency_overrides[get_session] = override_session

    try:
        response = TestClient(app).post(
            "/api/tasks/factors/analyze",
            json={
                "factor_name": "roe",
                "period_start": "2026-05-01",
                "period_end": "2026-05-22",
                "forward_days": 10,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"task_id": "factor-task-2", "status": "pending"}
    assert fake_session.params[0]["task_name"] == "analyze_factor_icir"
    apply_async.assert_called_once_with(
        kwargs={
            "factor_name": "roe",
            "period_start": "2026-05-01",
            "period_end": "2026-05-22",
            "forward_days": 10,
        },
        task_id="factor-task-2",
    )
