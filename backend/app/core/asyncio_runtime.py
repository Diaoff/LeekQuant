"""Process-wide asyncio event loop helper for Celery worker tasks.

Why this exists
---------------
Celery worker processes are synchronous, but our task bodies use asyncio to
drive the async SQLAlchemy engine (``app.db.session.engine`` is created at
module import time). Calling ``asyncio.run(coro)`` per task **creates and
closes a brand-new event loop every time**. A module-level async engine binds
its pooled connections to the first loop they touch; once that loop is closed
and a new one is created, reusing a pooled connection raises
``RuntimeError: ... attached to a different loop``.

That error was the real root cause of "subtasks stuck in pending": a batch
would fail mid-sync with the loop error, then its failure-report
(``asyncio.run`` again) and the signal-based reconcile (``asyncio.run`` again)
hit the *same* error, so the task_runs row never flipped to ``failed`` and
stayed ``pending`` forever.

Fix
---
Keep ONE event loop alive per worker process (created in ``worker_process_init``)
and run every coroutine on it via ``run_async`` WITHOUT closing it. The engine
stays bound to a single consistent loop for the process lifetime.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging

logger = logging.getLogger(__name__)


def get_loop() -> asyncio.AbstractEventLoop:
    """Return the process-wide event loop, creating/setting one if needed."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _run_in_new_loop(coro):
    """Run a coroutine in an isolated fresh loop (used when one is already running)."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _cancel_stray_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel any tasks left pending after a coroutine completed.

    Mirrors the cleanup ``asyncio.run`` performs (minus closing the loop) so
    stray tasks don't leak across invocations on this long-lived loop.
    """
    try:
        tasks = asyncio.all_tasks(loop)
    except RuntimeError:
        return
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    try:
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
    except Exception:  # pragma: no cover - best-effort hygiene
        pass


def run_async(coro):
    """Run a coroutine to completion on the process-wide loop (no loop close).

    Drop-in replacement for ``asyncio.run`` in Celery task bodies / signal
    handlers. Falls back to an isolated thread if a loop is already running in
    this thread (e.g. pytest-asyncio), to avoid cross-loop collisions.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = get_loop()
        try:
            result = loop.run_until_complete(coro)
        finally:
            _cancel_stray_tasks(loop)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_run_in_new_loop, coro).result()
