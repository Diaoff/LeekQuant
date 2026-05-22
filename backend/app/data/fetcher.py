from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import TypeAlias

import redis as redis_mod
from requests.exceptions import ConnectionError as ReqConnectionError

from app.core.config import get_settings
from app.data.providers import (
    ADataProvider,
    AkShareProvider,
    BaostockProvider,
    DataProvider,
    DataProviderError,
)

__all__ = [
    "DataProvider", "DataProviderError",
    "configure_providers",
    "default_providers", "stock_basic_providers",
    "fetch_with_fallback", "get_data_proxy_url",
]

ProviderList: TypeAlias = Iterable[DataProvider]

_RETRYABLE = (ReqConnectionError, TimeoutError, OSError)
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_REDIS_CONFIG_KEY = "leek:provider_order"
_PROVIDER_ORDER: list[str] = ["adata", "baostock", "akshare"]
_REDIS_CLIENT: redis_mod.Redis | None = None


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
    return [_PROVIDER_MAP[n]() for n in order if n in _PROVIDER_MAP]

def stock_basic_providers() -> list[DataProvider]:
    return default_providers()


_PROVIDER_MAP: dict[str, type[DataProvider]] = {
    "adata": ADataProvider,
    "baostock": BaostockProvider,
    "akshare": AkShareProvider,
}


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
) -> tuple[str, list]:
    errors: list[str] = []
    with _data_proxy_ctx(proxy_url):
        for provider in providers:
            for attempt in range(2):
                try:
                    records = _try_once(provider, method_name, args)
                    if records:
                        return provider.name, records
                    errors.append(f"{provider.name}: no records returned")
                    break
                except _RETRYABLE as exc:
                    msg = f"{provider.name}: {exc}"
                    if attempt == 0:
                        time.sleep(1)
                        continue
                    errors.append(msg)
                    break
                except Exception as exc:
                    errors.append(f"{provider.name}: {exc}")
                    break
    raise DataProviderError("; ".join(errors) or "all providers failed")
