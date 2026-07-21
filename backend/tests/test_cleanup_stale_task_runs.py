"""Tests for cleanup_stale_task_runs periodic beat task (P1-11)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.tasks import data_tasks as data_tasks_module


class FakeSession:
    """Async context manager that yields self."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture(autouse=True)
def _stub_beat_lock(monkeypatch):
    """Stub BeatLock so the task actually runs (lock acquire returns True)."""
    from app.tasks import beat_lock as beat_lock_module

    class _OkLock:
        def acquire(self, _name: str) -> bool:
            return True

        def release(self, _name: str) -> None:
            return None

    monkeypatch.setattr(beat_lock_module, "get_beat_lock", lambda: _OkLock())


def test_cleanup_calls_mark_stale_with_configured_threshold(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_mark(session, *, older_than, error_message):
        captured["older_than"] = older_than
        captured["error_message"] = error_message
        return 3

    # Patch the in-function import target (app.data.repository.mark_stale_running_task_runs)
    import app.data.repository as repo_module

    monkeypatch.setattr(repo_module, "mark_stale_running_task_runs", fake_mark)

    # Stub async_session_factory() -> async context manager (imported inside the task)
    import app.db.session as session_module

    monkeypatch.setattr(session_module, "async_session_factory", lambda: FakeSession())

    # Override stale_task_run_hours via settings patch
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "stale_task_run_hours", 5)

    result = data_tasks_module.cleanup_stale_task_runs()

    assert result == {"cleaned": 3, "stale_hours": 5}
    assert captured["older_than"] == timedelta(hours=5)
    assert captured["error_message"] == "stale running task after periodic cleanup"


def test_cleanup_returns_zero_when_no_stale_records(monkeypatch):
    async def fake_mark(session, *, older_than, error_message):
        return 0

    import app.data.repository as repo_module

    monkeypatch.setattr(repo_module, "mark_stale_running_task_runs", fake_mark)
    import app.db.session as session_module

    monkeypatch.setattr(session_module, "async_session_factory", lambda: FakeSession())

    result = data_tasks_module.cleanup_stale_task_runs()

    assert result == {"cleaned": 0, "stale_hours": 2}  # default threshold


def test_cleanup_registered_in_beat_schedule():
    """Verify the hourly beat schedule entry exists."""
    from app.tasks.celery_app import celery_app

    assert "cleanup-stale-task-runs-hourly" in celery_app.conf.beat_schedule
    entry = celery_app.conf.beat_schedule["cleanup-stale-task-runs-hourly"]
    assert entry["task"] == "app.tasks.data_tasks.cleanup_stale_task_runs"


def test_cleanup_registered_as_celery_task():
    """Verify the task is registered under the expected name."""
    from app.tasks.celery_app import celery_app

    assert "app.tasks.data_tasks.cleanup_stale_task_runs" in celery_app.tasks
