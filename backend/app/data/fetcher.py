from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import TypeAlias

import redis as redis_mod
from requests.exceptions import ConnectionError as ReqConnectionError
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.error import URLError

from app.core.config import get_settings
from app.data.providers import (
    DataProvider,
    DataProviderError,
    METHOD_CAPABILITIES,
    PROVIDER_REGISTRY,
    provider_supports,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DataProvider", "DataProviderError",
    "configure_providers",
    "default_providers", "providers_for_capability", "providers_for_method", "stock_basic_providers",
    "fetch_with_fallback", "fetch_with_fallback_short", "fetch_union", "filter_open_circuits", "get_data_proxy_url",
]

ProviderList: TypeAlias = Iterable[DataProvider]

# Retryable errors — expanded to include URLError so transient network errors
# (which _http_json previously wrapped as non-retryable DataProviderError) are retried.
_RETRYABLE = (ReqConnectionError, TimeoutError, OSError, URLError)
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_REDIS_CONFIG_KEY = "leek:provider_order"
_PROVIDER_ORDER: list[str] = [
    cls.name for cls in sorted(PROVIDER_REGISTRY.values(), key=lambda provider_cls: provider_cls.priority_default)
]
_REDIS_CLIENT: redis_mod.Redis | None = None

async def filter_open_circuits(
    session: AsyncSession,
    providers: list[DataProvider],
    data_type: str,
) -> list[DataProvider]:
    """Return ``providers`` unchanged.

    The circuit breaker that previously short-circuited failing providers
    (it read ``data_update_state.failure_count``) has been removed — the
    ``data_update_state`` table no longer exists. Provider health is now
    handled by the kline-sync DB queue's per-item retry / permanent-failure
    mechanism, so no upfront provider filtering is needed here.
    """
    return list(providers)


def _get_redis() -> redis_mod.Redis | None:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        try:
            _REDIS_CLIENT = redis_mod.from_url(get_settings().redis_url, socket_connect_timeout=1)
        except Exception:
            return None
    return _REDIS_CLIENT


def _load_order_from_redis() -> list[str] | None:
    try:
        client = _get_redis()
        if client is None:
            return None
        data = client.get(_REDIS_CONFIG_KEY)
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


def configure_providers(ordered_names: list[str]) -> None:
    _PROVIDER_ORDER.clear()
    _PROVIDER_ORDER.extend(ordered_names)
    try:
        client = _get_redis()
        if client is not None:
            client.set(_REDIS_CONFIG_KEY, json.dumps(ordered_names))
    except Exception:
        pass


def default_providers() -> list[DataProvider]:
    order = _load_order_from_redis() or _PROVIDER_ORDER
    seen = set()
    result = []
    for n in order:
        if n in PROVIDER_REGISTRY and n not in seen:
            result.append(PROVIDER_REGISTRY[n]())
            seen.add(n)
    for n, cls in PROVIDER_REGISTRY.items():
        if n not in seen:
            result.append(cls())
            seen.add(n)
    return result


def providers_for_capability(capability: str) -> list[DataProvider]:
    return [provider for provider in default_providers() if provider_supports(provider, capability)]


def providers_for_method(method_name: str) -> list[DataProvider]:
    capability = METHOD_CAPABILITIES.get(method_name)
    if capability is None:
        return default_providers()
    return providers_for_capability(capability)


def stock_basic_providers() -> list[DataProvider]:
    return providers_for_method("fetch_stock_basic")


@contextmanager
def _data_proxy_ctx(proxy_url: str | None) -> Iterator[None]:
    if not proxy_url:
        yield
        return
    saved = {k: os.environ.get(k) for k in _PROXY_KEYS}
    for k in _PROXY_KEYS:
        os.environ[k] = proxy_url
    try:
        yield
    finally:
        for k in _PROXY_KEYS:
            v = saved[k]
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def get_data_proxy_url() -> str | None:
    return get_settings().data_proxy_url


_PING_SAMPLE_CODE = "000001.SZ"


async def ping_providers(
    session: AsyncSession,
    providers: list[DataProvider],
    data_type: str,
    *,
    sample_ts_code: str = _PING_SAMPLE_CODE,
) -> list[DataProvider]:
    """Test each provider with a single stock, return only working providers.

    Runs a quick health check by fetching one stock's K-line for the last 5
    days from each provider.

    MUST be called from async context before ``asyncio.to_thread``.

    Returns:
        Filtered list of providers that returned data.
    """
    settings = get_settings()
    today = datetime.now(tz=UTC).date()
    start = today - timedelta(days=10)
    end = today

    working: list[DataProvider] = []
    capability = METHOD_CAPABILITIES.get("fetch_daily_kline", "daily_kline")

    for provider in providers:
        if not provider_supports(provider, capability):
            continue
        try:
            result = await asyncio.to_thread(
                fetch_with_fallback_short,
                [provider],
                "fetch_daily_kline",
                sample_ts_code,
                start,
                end,
                proxy_url=get_data_proxy_url(),
            )
            if result is not None:
                working.append(provider)
            else:
                logger.warning("provider %s ping failed for %s", provider.name, sample_ts_code)
        except Exception as exc:
            logger.warning("provider %s ping raised: %s", provider.name, exc)

    if not working:
        logger.error("ALL providers failed ping health-check for %s", data_type)
    else:
        logger.info(
            "provider health-check for %s: %d/%d working — %s",
            data_type, len(working), len(providers),
            [p.name for p in working],
        )

    return working


def _try_once(provider: DataProvider, method_name: str, args: tuple) -> list | None:
    method = getattr(provider, method_name)
    records = method(*args)
    return records if records else None


def fetch_with_fallback(
    providers: ProviderList,
    method_name: str,
    *args,
    proxy_url: str | None = None,
) -> tuple[str, list]:
    """Fetch with provider fallback + retry. Synchronous — runs in a worker
    thread when called from async code via ``asyncio.to_thread``.

    Circuit-breaker check is intentionally NOT done here: it requires an
    ``AsyncSession`` and we are in a worker thread. Callers MUST filter
    open-circuit providers beforehand using ``filter_open_circuits`` (async).

    Args:
        providers: Ordered list of providers (primary first).
        method_name: Provider method to call (e.g. "fetch_kline").
        *args: Positional args passed to the provider method.
        proxy_url: Optional HTTP proxy for Chinese data sources.

    Returns:
        (provider_name, records) tuple from the first successful provider.

    Raises:
        DataProviderError: All providers failed or returned empty.
    """
    errors: list[str] = []
    capability = METHOD_CAPABILITIES.get(method_name)
    provider_list = [
        provider for provider in providers if capability is None or provider_supports(provider, capability)
    ]
    if not provider_list:
        raise DataProviderError(f"no enabled providers support {capability or method_name}")

    settings = get_settings()
    max_retries = settings.data_max_retries

    with _data_proxy_ctx(proxy_url):
        for provider in provider_list:
            for attempt in range(max_retries):
                try:
                    records = _try_once(provider, method_name, args)
                    if records:
                        return provider.name, records
                    errors.append(f"{provider.name}: no records returned")
                    break
                except _RETRYABLE as exc:
                    msg = f"{provider.name}: {exc}"
                    if attempt < max_retries - 1:
                        # Exponential backoff with full jitter: 2^attempt + [0, 1)
                        backoff = (2 ** attempt) + random.random()
                        time.sleep(min(backoff, 30.0))
                        continue
                    errors.append(msg)
                    break
                except Exception as exc:
                    errors.append(f"{provider.name}: {exc}")
                    break
    raise DataProviderError("; ".join(errors) or "all providers failed")


def fetch_with_fallback_short(
    providers: ProviderList,
    method_name: str,
    *args,
    proxy_url: str | None = None,
    max_attempts: int = 1,
) -> tuple[str, list] | None:
    """Like fetch_with_fallback but with NO retries and NO exception.

    Used for health-check pings: returns (name, records) on first success,
    None if all providers fail. Never raises — the caller decides what to do
    with a None result.
    """
    capability = METHOD_CAPABILITIES.get(method_name)
    provider_list = [
        provider for provider in providers if capability is None or provider_supports(provider, capability)
    ]
    if not provider_list:
        return None

    with _data_proxy_ctx(proxy_url):
        for provider in provider_list:
            for attempt in range(max_attempts):
                try:
                    method = getattr(provider, method_name)
                    records = method(*args)
                    if records:
                        return provider.name, records
                except Exception:
                    break
    return None


def fetch_union(
    providers: ProviderList,
    method_name: str,
    *args,
    proxy_url: str | None = None,
) -> tuple[list[str], list]:
    """Fetch from ALL providers and union their records (deduped by ts_code).

    Unlike :func:`fetch_with_fallback` (which returns the first non-empty
    provider), this collects records from every provider and merges them.
    Used by ``sync_stock_basic`` where *completeness* matters more than
    single-source purity: e.g. AData's ``all_code()`` returns only ~990 rows
    while AkShare returns the full ~5900-row A-share universe, so stopping at
    the first non-empty provider would silently truncate the stock list.

    First occurrence of a ``ts_code`` wins; later duplicates are dropped.

    Returns:
        (list_of_source_names, unioned_records).
    """
    capability = METHOD_CAPABILITIES.get(method_name)
    provider_list = [
        provider for provider in providers if capability is None or provider_supports(provider, capability)
    ]
    if not provider_list:
        raise DataProviderError(f"no enabled providers support {capability or method_name}")

    settings = get_settings()
    max_retries = settings.data_max_retries

    seen: set[str] = set()
    unioned: list = []
    sources: list[str] = []

    with _data_proxy_ctx(proxy_url):
        for provider in provider_list:
            for attempt in range(max_retries):
                try:
                    method = getattr(provider, method_name)
                    records = method(*args)
                except _RETRYABLE as exc:
                    if attempt < max_retries - 1:
                        backoff = (2 ** attempt) + random.random()
                        time.sleep(min(backoff, 30.0))
                        continue
                    break
                except Exception:
                    # Non-retryable provider error: skip this provider,
                    # continue with the next one (resilient to one bad source).
                    break
                if not records:
                    break
                sources.append(provider.name)
                for record in records:
                    key = getattr(record, "ts_code", None)
                    if key is None or key not in seen:
                        if key is not None:
                            seen.add(key)
                        unioned.append(record)
                break  # success for this provider; move on
    if not unioned:
        raise DataProviderError(f"all providers returned no records for {method_name}")
    return sources, unioned
