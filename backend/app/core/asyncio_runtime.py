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
import os

logger = logging.getLogger(__name__)

# Track the PID of the process that created the event loop.  After fork(), the
# child inherits the parent's loop object (including its kqueue selector fd).
# Using a selector fd opened in a different process causes OSError: [Errno 9]
# Bad file descriptor once either process closes the shared fd.  We detect the
# fork by comparing PID and create a fresh loop with a new selector.
_loop_pid: int | None = None


def get_loop() -> asyncio.AbstractEventLoop:
    """Return the process-wide event loop, creating/setting one if needed."""
    global _loop_pid
    current_pid = os.getpid()

    if _loop_pid is not None and _loop_pid != current_pid:
        # Fork detected -- the inherited loop's selector (kqueue fd) is shared
        # with the parent and will become invalid once either side closes it.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop_pid = current_pid
        return loop

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        logger.warning("silent except in get_loop", exc_info=True)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop_pid = current_pid
        return loop
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    _loop_pid = current_pid
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


def _renew_selector_if_stale(loop: asyncio.AbstractEventLoop) -> None:
    """Replace the loop's selector if its fd is stale (e.g. after fork)."""
    import selectors
    selector = getattr(loop, "_selector", None)
    if selector is None:
        return
    try:
        selector.get_map()
    except (OSError, ValueError):
        logger.debug("silent except in _renew_selector_if_stale")
        pass
    else:
        return  # selector is healthy
    new_selector = selectors.DefaultSelector()
    loop._selector = new_selector
    logger.debug("Replaced stale selector for pid %d", os.getpid())


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
        _renew_selector_if_stale(loop)
        # Wrap the coroutine in a single task so we can track and clean up only
        # *this* invocation's work. We must NOT cancel every task on the loop
        # (the previous _cancel_stray_tasks did), because the process-wide
        # asyncpg connection pool keeps background reader tasks registered on
        # this same loop — cancelling them silently kills pooled connections and
        # raises ConnectionDoesNotExistError / no running loop on the next call.
        main_task = asyncio.ensure_future(coro, loop=loop)
        try:
            result = loop.run_until_complete(main_task)
        except BaseException:
            # Ensure the main coroutine is not left pending (leaking across
            # invocations). Only cancel our own task, never the loop-global ones.
            if not main_task.done():
                main_task.cancel()
                try:
                    loop.run_until_complete(asyncio.gather(main_task, return_exceptions=True))
                except Exception:  # pragma: no cover - best-effort hygiene
                    logger.debug("silent except in run_async")
                    pass
            raise
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_run_in_new_loop, coro).result()
