"""Cached Celery worker health probes.

``celery_app.control.inspect()`` round-trips to the broker and is somewhat
expensive, yet worker/queue state changes slowly (seconds to minutes).
These helpers cache the result for a short TTL so repeated status/health
requests don't hammer the broker. On a transient failure we serve the last
good value (stale is acceptable for health display); only when there is no
cached value do we surface a 503.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, status

from app.tasks.celery_app import celery_app

# Queue/worker state changes slowly; 10s is plenty for a health display.
_TTL_SECONDS = 10.0

_cache: dict[str, tuple[float, Any]] = {}


def cached_active_queues() -> dict:
    """Return celery ``active_queues()``, cached for ``_TTL_SECONDS``."""
    key = "active_queues"
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None and (now - entry[0]) <= _TTL_SECONDS:
        return entry[1]
    try:
        value = celery_app.control.inspect(timeout=1.0).active_queues()
    except Exception as exc:
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"celery worker health check failed: {exc}",
            ) from exc
        # Serve stale value on transient broker failure.
        return entry[1]
    _cache[key] = (now, value)
    return value
