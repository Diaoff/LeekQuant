import asyncio

from app.tasks.celery_app import celery_app
from app.tasks.data_tasks import _run_tracked


def test_celery_app_registers_data_tasks() -> None:
    celery_app.loader.import_default_modules()

    registered = set(celery_app.tasks)

    assert "app.tasks.data_tasks.update_stock_basic" in registered
    assert "app.tasks.data_tasks.update_trade_calendar" in registered
    assert "app.tasks.data_tasks.sync_sample_kline" in registered
    assert "app.tasks.data_tasks.incremental_kline_update" in registered


def test_run_tracked_claims_pending_task_run(monkeypatch) -> None:
    from app.tasks import data_tasks

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
    monkeypatch.setattr(data_tasks, "async_session_factory", lambda: factory)

    async def run():
        return await _run_tracked("sync_sample_kline", "task-123", {}, lambda _session: _success())

    async def _success():
        return {"ok": True}

    result = asyncio.run(run())

    assert result == {"ok": True}
    assert "UPDATE task_runs" in factory.session.statements[0]
    assert factory.session.params[0]["task_id"] == "task-123"
    assert factory.session.params[-1]["status"] == "success"
