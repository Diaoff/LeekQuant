"""Guard against the 'different loop' Celery crash.

In-worker Celery task bodies must run asyncio coroutines via ``run_async``
(app.core.asyncio_runtime), which reuses the worker's persistent event loop
created in ``worker_process_init``. Using ``asyncio.run`` instead creates and
closes a fresh loop each call; the module-level asyncpg pool / aiohttp connector
is bound to the first loop it touched, so reusing it raises
``RuntimeError: ... attached to a different loop`` — which used to leave tasks
stuck in ``pending``/``running`` forever (see backtest & signal generation bugs).

This test fails if any Celery task module wraps a body in ``asyncio.run``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
TASK_MODULES = [
    "app/tasks/signal_tasks.py",
    "app/tasks/trading_tasks.py",
    "app/tasks/data_tasks.py",
]


def test_celery_task_modules_use_run_async_not_asyncio_run() -> None:
    for rel in TASK_MODULES:
        path = BACKEND_ROOT / rel
        assert path.exists(), f"expected task module missing: {rel}"
        src = path.read_text(encoding="utf-8")
        assert (
            "asyncio.run(" not in src
        ), f"{rel} must not call asyncio.run (causes 'different loop'); use run_async instead"
        assert "run_async(" in src, f"{rel} should drive coroutines via run_async"


def test_run_async_does_not_cancel_loop_global_tasks() -> None:
    """Regression: run_async must not kill background tasks bound to the process loop.

    The asyncpg connection pool keeps a background reader task registered on the
    worker process loop. A previous implementation cancelled *every* task on the
    loop after each call, silently killing pooled connections and raising
    ``ConnectionDoesNotExistError`` (and "no running event loop") on the next
    ``run_async`` call — this is exactly what broke backtest #148.
    """
    from app.core.asyncio_runtime import run_async, get_loop

    loop = get_loop()

    async def _background():
        # Mimics an asyncpg connection reader task: lives for the loop lifetime.
        await asyncio.sleep(10)

    bg = asyncio.ensure_future(_background(), loop=loop)

    async def _work():
        await asyncio.sleep(0)
        return "ok"

    # Two sequential run_async calls — must not cancel the background task.
    assert run_async(_work()) == "ok"
    assert run_async(_work()) == "ok"

    assert not bg.done(), "run_async cancelled a loop-global task (would kill asyncpg conns)"
    bg.cancel()
