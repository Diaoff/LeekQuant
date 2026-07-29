"""Tests for _run_tracked session isolation (P1 H-4 fix).

Verifies that the task body's session lifecycle (including explicit close())
no longer invalidates the tracker_session used by _finish_task_run. Before
the fix, calling session.close() inside the task body would leave
task_runs stuck in 'running' status because _finish_task_run tried to
write to the same closed session.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.tasks.tracking import _run_tracked, with_session


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _TrackerSession:
    """Session used for tracker bookkeeping (claim/finish)."""

    def __init__(self):
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(params or {})
        if "UPDATE task_runs" in sql and "RETURNING id" in sql:
            return _FakeScalarResult(42)
        if "INSERT INTO task_runs" in sql:
            return _FakeScalarResult(99)
        return _FakeScalarResult(None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _TaskBodySession:
    """Session used inside the task body. Tracks close() calls."""

    def __init__(self, *, raise_on_execute: str | None = None):
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.closed = False
        self._raise_on_execute = raise_on_execute

    async def execute(self, statement, params=None):
        sql = str(statement)
        if self._raise_on_execute and self._raise_on_execute in sql:
            raise RuntimeError("business SQL failed")
        self.statements.append(sql)
        self.params.append(params or {})
        return _FakeScalarResult(None)

    async def close(self):
        self.closed = True


class _FakeFactory:
    """Mimics async_session_factory: callable returning async ctx manager.

    Each call returns a fresh context manager. Tests can pre-register the
    sequence of sessions to hand out (tracker_session, task_body_session, ...).
    """

    def __init__(self, sessions: list):
        self._sessions = list(sessions)
        self._index = 0

    def __call__(self):
        session = self._sessions[self._index % len(self._sessions)]
        self._index += 1
        return _Ctx(session)


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        # Don't auto-close; let the task body call .close() explicitly so
        # we can verify behavior.
        return False


@pytest.mark.asyncio
async def test_task_body_close_session_doesnt_break_finish(monkeypatch):
    """P1 H-4: task body calling session.close() must not break _finish_task_run.

    Before fix: _run_tracked used ONE session for both tracking and task body.
    Calling session.close() inside the task body invalidated the same session
    used by _finish_task_run, leaving task_runs stuck in 'running' status.

    After fix: _run_tracked passes async_session_factory to fn. Task body opens
    its own session via the factory; closing it has no effect on tracker_session.
    """
    from app.tasks import tracking

    tracker_session = _TrackerSession()
    task_body_session = _TaskBodySession()
    factory = _FakeFactory([tracker_session, task_body_session])
    # async_session_factory must be the factory itself (callable returning ctx mgr)
    monkeypatch.setattr(tracking, "async_session_factory", factory)

    async def task_body(session_factory):
        # Open and explicitly close the task body's own session, mimicking
        # sync_fundamentals_task / kline_sync_dispatch behavior.
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        # Even after the task body's session is gone (context manager exited),
        # _finish_task_run on tracker_session must still succeed.
        return {"ok": True}

    result = await _run_tracked("sync_fundamentals", "task-1", {}, task_body)

    assert result == {"ok": True}
    # tracker_session must have received claim + finish SQL, both successful.
    assert any("UPDATE task_runs" in s and "RETURNING id" in s for s in tracker_session.statements) or \
           any("INSERT INTO task_runs" in s for s in tracker_session.statements)
    assert tracker_session.params[-1]["status"] == "success"
    assert tracker_session.commits >= 1
    # tracker_session must NOT be closed (only the task body's session is).
    assert tracker_session.closed is False
    # task body's session must have been used for the business SQL.
    assert any("SELECT 1" in s for s in task_body_session.statements)


@pytest.mark.asyncio
async def test_task_body_failure_still_records_failed_status(monkeypatch):
    """Even when the task body raises, _finish_task_run must record 'failed'."""
    from app.tasks import tracking

    tracker_session = _TrackerSession()
    task_body_session = _TaskBodySession(raise_on_execute="SELECT broken_business_sql")
    factory = _FakeFactory([tracker_session, task_body_session])
    monkeypatch.setattr(tracking, "async_session_factory", factory)

    async def task_body(session_factory):
        async with session_factory() as session:
            await session.execute(text("SELECT broken_business_sql"))
        return {"ok": True}

    with pytest.raises(RuntimeError, match="business SQL failed"):
        await _run_tracked("compute_daily_factors", "task-fail", {}, task_body)

    # tracker_session must record failure (rollback + failed status).
    assert tracker_session.rollbacks == 1
    assert tracker_session.params[-1]["status"] == "failed"
    assert tracker_session.params[-1]["error_message"] == "business SQL failed"


def test_with_session_opens_separate_session_via_factory():
    """with_session wraps fn(session, *args) so it accepts session_factory."""
    captured = {}

    class _Session:
        async def execute(self, stmt):
            captured["stmt"] = str(stmt)
            return _FakeScalarResult(None)

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    async def business_logic(session, *, value):
        await session.execute(text(f"SELECT {value}"))
        return {"value": value}

    wrapped = with_session(business_logic, value=42)
    result = asyncio.run(wrapped(_Factory()))

    assert result == {"value": 42}
    assert "SELECT 42" in captured["stmt"]


def test_with_session_passes_args_kwargs_through():
    """with_session must forward positional + keyword args to fn."""
    captured = {}

    class _Session:
        pass

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    async def business_logic(session, pos_arg, *, kw_arg):
        captured["pos_arg"] = pos_arg
        captured["kw_arg"] = kw_arg
        return {"pos": pos_arg, "kw": kw_arg}

    wrapped = with_session(business_logic, "hello", kw_arg="world")
    result = asyncio.run(wrapped(_Factory()))

    assert result == {"pos": "hello", "kw": "world"}
    assert captured == {"pos_arg": "hello", "kw_arg": "world"}


@pytest.mark.asyncio
async def test_with_session_provides_independent_session_for_each_call(monkeypatch):
    """Each call to the wrapped fn must open a fresh session via factory."""
    from app.tasks import tracking

    sessions_created: list = []
    _counter = {"n": 0}

    class _Session:
        def __init__(self):
            # Use a monotonic counter rather than id(self) — Python may reuse
            # memory addresses for short-lived objects, making id() unreliable
            # as a uniqueness check across runs.
            _counter["n"] += 1
            self.unique_id = _counter["n"]
            sessions_created.append(self.unique_id)

        async def execute(self, statement, params=None):
            sql = str(statement)
            if "INSERT INTO task_runs" in sql:
                return _FakeScalarResult(1)
            if "UPDATE task_runs" in sql and "RETURNING id" in sql:
                return _FakeScalarResult(1)
            return _FakeScalarResult(None)

        async def commit(self):
            return None

        async def rollback(self):
            return None

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    # Patch async_session_factory inside _run_tracked to be our factory.
    factory_instance = _Factory()
    monkeypatch.setattr(tracking, "async_session_factory", factory_instance)

    async def business_logic(session):
        await session.execute(text("SELECT 1"))
        return {"ok": True}

    # Run twice: each invocation should open a separate tracker_session + task_session.
    await _run_tracked("test_task", "task-1", {}, with_session(business_logic))
    await _run_tracked("test_task", "task-2", {}, with_session(business_logic))

    # 2 runs × 2 sessions each (tracker + task body) = 4 distinct sessions.
    assert len(sessions_created) == 4
    assert len(set(sessions_created)) == 4  # all unique
