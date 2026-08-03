"""Redis-backed K-line cache for backtest performance.

Eliminates redundant DB queries when the same stock pool + date range is
backtested multiple times (e.g., strategy parameter tuning).

The cache stores *raw row dicts* (the daily_kline columns), NOT pickled
KBar objects. The backtest engine always rebuilds KBar via ``_parse_kline_rows``
from these dicts, so the cached and DB paths produce byte-identical engine
inputs and we avoid any dataclass/slots (``@dataclass(slots=True)``) pickle
round-trip corruption that previously caused ``IndexError`` in the engine.

Serialization uses pickle (efficient for plain dicts / Decimal / date). Redis
connection is established lazily per call to avoid holding idle connections.
"""
from __future__ import annotations

import hashlib
import logging
import pickle
from datetime import date
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Pickle protocol 5 (Python 3.8+) for efficient large-object serialization
_PICKLE_PROTOCOL = 5

# Default TTL: 1 hour. Repeated backtests within the window benefit; stale
# data is automatically evicted.
_DEFAULT_TTL_SECONDS = 3600

# Key prefix for all backtest kline cache entries.
# Bumped to v2 when the cached payload changed from pickled KBar lists to
# raw row dicts (see module docstring). This forces an immediate cold-cache
# for any entry written by the old schema, avoiding a schema-mismatch
# TypeError when a stale v1 entry is hit within its TTL.
_KEY_PREFIX = "backtest:klines:v2:"


def _build_cache_key(stock_codes: list[str], start_date: date, end_date: date) -> str:
    """Build a deterministic cache key from stock codes and date range.

    The stock codes are sorted and hashed to keep the key short regardless
    of pool size. The date range is appended in ISO format for readability.
    """
    raw = ",".join(sorted(stock_codes))
    codes_hash = hashlib.md5(raw.encode("ascii")).hexdigest()
    return f"{_KEY_PREFIX}{codes_hash}:{start_date.isoformat()}:{end_date.isoformat()}"


def _encode_klines(klines: dict[str, list[dict[str, Any]]]) -> bytes:
    """Serialize raw kline row dicts to bytes via pickle."""
    return pickle.dumps(klines, protocol=_PICKLE_PROTOCOL)


def _decode_klines(data: bytes) -> dict[str, list[dict[str, Any]]] | None:
    """Deserialize raw kline row dicts from pickle bytes."""
    try:
        return pickle.loads(data)
    except Exception as exc:
        logger.warning("Failed to decode cached klines: %s", exc)
        return None


async def get_cached_klines(
    stock_codes: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, list[dict[str, Any]]] | None:
    """Try to load raw kline rows from Redis cache.

    The cache stores plain row dicts (matching the daily_kline columns), NOT
    pickled KBar objects, so the engine always rebuilds KBar via
    ``_parse_kline_rows`` — identical to the DB path. This avoids any
    dataclass/slots pickle round-trip corruption.

    Returns the deserialized dict or None on cache miss / error.
    """
    key = _build_cache_key(stock_codes, start_date, end_date)
    try:
        import redis.asyncio as redis_async
        settings = get_settings()
        client = redis_async.from_url(
            settings.redis_url,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=False,
        )
        data = await client.get(key)
        await client.aclose()
        if data is None:
            return None
        decoded = _decode_klines(data)
        if decoded is not None:
            logger.debug("Redis kline cache HIT: %s", key)
        return decoded
    except Exception as exc:
        # Cache miss/error is non-fatal; caller falls back to DB.
        logger.debug("Redis kline cache miss for %s: %s", key, exc)
        return None


async def set_cached_klines(
    stock_codes: list[str],
    start_date: date,
    end_date: date,
    klines: dict[str, list[dict[str, Any]]],
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> bool:
    """Store raw kline rows in Redis cache.

    ``klines`` is a dict of ts_code -> list of plain row dicts (daily_kline
    columns). Returns True on success, False on failure (non-fatal).
    """
    key = _build_cache_key(stock_codes, start_date, end_date)
    try:
        import redis.asyncio as redis_async
        settings = get_settings()
        client = redis_async.from_url(
            settings.redis_url,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=False,
        )
        encoded = _encode_klines(klines)
        await client.setex(key, ttl_seconds, encoded)
        await client.aclose()
        logger.debug("Redis kline cache SET: %s (%d bytes)", key, len(encoded))
        return True
    except Exception as exc:
        logger.debug("Redis kline cache set failed for %s: %s", key, exc)
        return False


async def invalidate_cache(stock_codes: list[str], start_date: date, end_date: date) -> bool:
    """Remove a specific kline cache entry (e.g., after data refresh)."""
    key = _build_cache_key(stock_codes, start_date, end_date)
    try:
        import redis.asyncio as redis_async
        settings = get_settings()
        client = redis_async.from_url(
            settings.redis_url,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=False,
        )
        await client.delete(key)
        await client.aclose()
        return True
    except Exception as exc:
        logger.debug("Redis kline cache delete failed for %s: %s", key, exc)
        return False
