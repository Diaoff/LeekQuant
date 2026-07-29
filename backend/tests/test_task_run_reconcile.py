"""Tests for task_runs status reconciliation (P1-12 / SoftTimeLimitExceeded drift).

When a Celery task is killed by SoftTimeLimitExceeded, the in-body
``_finish_task_run`` DB write can be skipped, leaving the task_runs row stuck at
'running' while Celery marks the task FAILED in its result backend. The
``task_failure`` / ``task_success`` / ``task_revoked`` Celery signals now act as
the authoritative backstop that reconciles the task_runs row to the real
terminal state.
"""
from __future__ import annotations

import pytest


class _FakeResult:
    def fetchall(self):
        return []


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        self.params.append(params or {})
        return _FakeResult()

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_reconcile_task_run_status_only_touches_non_terminal():
    from app.data.repository import reconcile_task_run_status

    session = _FakeSession()
    await reconcile_task_run_status(
        session, task_id="t1", status="failed", error_message="boom"
    )

    assert any("UPDATE task_runs" in s for s in session.statements)
    # Idempotency guard: never overwrite a status the body already wrote.
    assert any("status IN ('pending', 'running')" in s for s in session.statements)
    assert session.params[-1]["status"] == "failed"
    assert session.params[-1]["error_message"] == "boom"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_reconcile_task_run_status_carries_result_on_success():
    from app.data.repository import reconcile_task_run_status

    session = _FakeSession()
    await reconcile_task_run_status(
        session, task_id="t2", status="success", result={"inserted": 5}
    )

    assert session.params[-1]["status"] == "success"
    assert session.params[-1]["result"] is not None


def test_task_failure_signal_reconciles(monkeypatch):
    import app.tasks.celery_app as ca

    calls: list[tuple] = []
    monkeypatch.setattr(
        ca, "_reconcile_task_run", lambda tid, st, **kw: calls.append((tid, st, kw))
    )

    ca.on_task_failure(sender=None, task_id="abc", exception=Exception("SoftTimeLimitExceeded"))

    assert calls == [("abc", "failed", {"error_message": "SoftTimeLimitExceeded"})]


def test_task_success_signal_reconciles(monkeypatch):
    import app.tasks.celery_app as ca

    calls: list[tuple] = []
    monkeypatch.setattr(
        ca, "_reconcile_task_run", lambda tid, st, **kw: calls.append((tid, st, kw))
    )

    ca.on_task_success(sender=None, result={"inserted": 7}, task_id="xyz")

    assert calls == [("xyz", "success", {"result": {"inserted": 7}})]
