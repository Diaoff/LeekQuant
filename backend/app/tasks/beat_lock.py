"""Distributed lock for Celery beat tasks (P1-10).

Prevents duplicate beat task execution when multiple beat instances run
(HA deployment). Uses Redis SET NX EX with Lua-script-based safe release.
"""
from __future__ import annotations

import logging
import uuid
from functools import wraps
from typing import Callable, TypeVar

import redis as redis_sync
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Per-process worker id; used as the lock value so we can verify ownership
# before releasing (avoids accidentally releasing another worker's lock).
_WORKER_ID = str(uuid.uuid4())

# Default TTL: must exceed task_time_limit (1800s) so the lock survives
# the longest single beat task; +60s cleanup headroom. Actual TTL is
# read from settings.beat_lock_ttl_seconds (defaults to this value).
DEFAULT_TTL_SECONDS = 1860


T = TypeVar("T")


class BeatLock:
    """Redis-backed distributed lock for beat tasks."""

    def __init__(
        self,
        redis_url: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self._redis_url = redis_url or settings.redis_url
        # Priority: explicit ttl_seconds > settings.beat_lock_ttl_seconds
        # > DEFAULT_TTL_SECONDS (last resort safety net; should never trigger
        # because Settings always provides a default).
        if ttl_seconds is not None:
            self._ttl = ttl_seconds
        else:
            self._ttl = getattr(
                settings,
                "beat_lock_ttl_seconds",
                DEFAULT_TTL_SECONDS,
            )
        self._client = redis_sync.from_url(
            self._redis_url,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=True,
        )
        # Lua script: only DEL if the stored value matches our worker id.
        # Prevents releasing a lock that has been acquired by another worker
        # after TTL expiry.
        self._release_script = self._client.register_script(
            """
            if redis.call("GET", KEYS[1]) == ARGV[1] then
                return redis.call("DEL", KEYS[1])
            else
                return 0
            end
            """
        )

    def acquire(self, task_name: str) -> bool:
        """Try to acquire the lock. Returns True on success, False if held."""
        key = self._key(task_name)
        try:
            ok = self._client.set(key, _WORKER_ID, nx=True, ex=self._ttl)
            return bool(ok)
        except RedisError as exc:
            # Fail-open: if Redis is down, log and proceed (avoid blocking
            # all scheduled tasks because the lock service is unavailable).
            logger.warning(
                "BeatLock.acquire failed for %s (fail-open): %s",
                task_name,
                exc,
            )
            return True

    def release(self, task_name: str) -> None:
        """Release the lock if we still own it."""
        key = self._key(task_name)
        try:
            self._release_script(keys=[key], args=[_WORKER_ID])
        except RedisError as exc:
            logger.warning("BeatLock.release failed for %s: %s", task_name, exc)

    def _key(self, task_name: str) -> str:
        return f"beat:lock:{task_name}"

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


_beat_lock: BeatLock | None = None


def get_beat_lock() -> BeatLock:
    """Singleton accessor for the BeatLock instance."""
    global _beat_lock
    if _beat_lock is None:
        _beat_lock = BeatLock()
    return _beat_lock


def reset_beat_lock_for_tests() -> None:
    """Test-only helper to reset the singleton."""
    global _beat_lock
    if _beat_lock is not None:
        _beat_lock.close()
        _beat_lock = None


def with_beat_lock(task_name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: acquire BeatLock before task body; skip if cannot acquire.

    Returns None when the lock is held by another worker. The Celery task
    should treat None as a successful no-op (do NOT raise, to avoid
    triggering retry backoff).
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            lock = get_beat_lock()
            if not lock.acquire(task_name):
                logger.info("skip duplicate beat run: %s", task_name)
                return None
            try:
                return func(*args, **kwargs)
            finally:
                lock.release(task_name)

        return wrapper

    return decorator
