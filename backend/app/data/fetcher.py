from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import TypeAlias

import redis as redis_mod
from requests.exceptions import ConnectionError as ReqConnectionError
from urllib.error import URLError

from app.core.config import get_settings
from app.data.circuit_breaker import CircuitBreaker
from app.data.providers import (
    DataProvider,
    DataProviderError,
    METHOD_CAPABILITIES,
    PROVIDER_REGISTRY,
    provider_supports,
)

__all__ = [
    "DataProvider", "DataProviderError",
    "configure_providers",
    "default_providers", "providers_for_capability", "providers_for_method", "stock_basic_providers",
    "fetch_with_fallback", "get_data_proxy_url",
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

# Module-level circuit breaker singleton (lazy-initialized)
_BREAKER: CircuitBreaker | None = None


def _get_breaker() -> CircuitBreaker:
    global _BREAKER
    if _BREAKER is None:
        _BREAKER = CircuitBreaker()
    return _BREAKER


def reset_breaker_for_tests() -> None:
    """Test helper — clear the cached circuit breaker singleton."""
    global _BREAKER
    _BREAKER = None


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


def _try_once(provider: DataProvider, method_name: str, args: tuple) -> list | None:
    method = getattr(provider, method_name)
    records = method(*args)
    return records if records else None


def fetch_with_fallback(
    providers: ProviderList,
    method_name: str,
    *args,
    proxy_url: str | None = None,
    data_type: str | None = None,
    session=None,
) -> tuple[str, list]:
    """Fetch with provider fallback, retry, and circuit breaker.

    Args:
        providers: Ordered list of providers (primary first).
        method_name: Provider method to call (e.g. "fetch_kline").
        *args: Positional args passed to the provider method.
        proxy_url: Optional HTTP proxy URL for Chinese data sources.
        data_type: If provided (and session too), enables circuit-breaker checks
            against `data_update_state.failure_count` for each provider.
        session: AsyncSession for circuit-breaker lookups. If None, breaker is bypassed.

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

    breaker = _get_breaker() if data_type else None
    settings = get_settings()
    max_retries = settings.data_max_retries

    with _data_proxy_ctx(proxy_url):
        for provider in provider_list:
            # Circuit breaker check — skip provider if open
            if breaker is not None and session is not None and data_type:
                try:
                    is_open = _breaker_sync_check(breaker, session, data_type, provider.name)
                except Exception:
                    is_open = False  # fail-open on breaker errors
                if is_open:
                    errors.append(f"{provider.name}: circuit open (skipped)")
                    continue

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


def _breaker_sync_check(breaker: CircuitBreaker, session, data_type: str, source: str) -> bool:
    """Synchronous wrapper for breaker.is_open — runs the coroutine via asyncio.

    Returns False on any error (fail-open).
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context — caller should pass an async-friendly session
            # Fall back to fail-open rather than blocking the event loop
            return False
        return loop.run_until_complete(breaker.is_open(session, data_type, source))
    except Exception:
        return False
