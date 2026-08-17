"""Redis-backed K-line cache for backtest performance.

Eliminates redundant DB queries when the same stock pool + date range is
backtested multiple times (e.g., strategy parameter tuning).

The cache stores *raw row dicts* (the daily_kline columns), NOT pickled
KBar objects. The backtest engine always rebuilds KBar via ``_parse_kline_rows``
from these dicts, so the cached and DB paths produce byte-identical engine
inputs and we avoid any dataclass/slots (``@dataclass(slots=True)``) pickle
round-trip corruption that previously caused ``IndexError`` in the engine.

The cache key is ``(sorted stock_codes, start_date, end_date)`` only. The
cached rows are the *raw* daily_kline rows (including ``adj_factor``); price
adjustment for ``adj_mode`` / ``fill_price_mode`` happens engine-side in
``_adjust_price``, so the payload is identical across adjust modes and the
key must NOT include them (doing so would only create redundant entries).

Serialization uses pickle (efficient for plain dicts / Decimal / date).
A single module-level async Redis client with a connection pool is reused
across calls; previously a new client was created and closed on every call,
which defeats pooling.
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

# Scan/delete batch size for cache invalidation.
_INVALIDATE_BATCH = 200


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


_client = None


def _get_redis():
    """Return a shared async Redis client (lazy, pooled).

    ``from_url`` does not open a connection eagerly, so this is cheap to call
    repeatedly. The connection pool is reused across get/set/invalidate so we
    no longer pay the connect/close cost per cache operation.
    """
    global _client
    if _client is None:
        import redis.asyncio as redis_async

        settings = get_settings()
        _client = redis_async.from_url(
            settings.redis_url,
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=False,
            health_check_interval=30,
        )
    return _client


async def close_kline_cache() -> None:
    """Close the shared Redis client (call on app shutdown)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None


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
        client = _get_redis()
        data = await client.get(key)
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
        client = _get_redis()
        encoded = _encode_klines(klines)
        await client.setex(key, ttl_seconds, encoded)
        logger.debug("Redis kline cache SET: %s (%d bytes)", key, len(encoded))
        return True
    except Exception as exc:
        logger.debug("Redis kline cache set failed for %s: %s", key, exc)
        return False


async def invalidate_all_kline_cache() -> int:
    """Invalidate the entire backtest kline cache.

    Called after a K-line data refresh so backtests never read stale rows.
    Because backtest cache keys are hashed per (stock_codes, date range) and
    a backtest may request an arbitrary subset, precise per-key invalidation
    is impractical; wiping the whole key space on refresh is safe because the
    cache is only read during backtests (infrequent) and repopulates on next
    run. Returns the number of keys deleted.
    """
    deleted = 0
    try:
        client = _get_redis()
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=f"{_KEY_PREFIX}*", count=_INVALIDATE_BATCH)
            if keys:
                await client.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        if deleted:
            logger.info("Invalidated %d backtest kline cache entries after data refresh", deleted)
    except Exception as exc:
        logger.warning("Failed to invalidate backtest kline cache: %s", exc)
    return deleted
