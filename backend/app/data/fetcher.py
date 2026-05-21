from __future__ import annotations

import os
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import TypeAlias

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
    "default_providers", "stock_basic_providers",
    "fetch_with_fallback", "get_data_proxy_url",
]

ProviderList: TypeAlias = Iterable[DataProvider]

_RETRYABLE = (ReqConnectionError, TimeoutError, OSError)
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


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


def default_providers() -> list[DataProvider]:
    return [ADataProvider(), BaostockProvider(), AkShareProvider()]

def stock_basic_providers() -> list[DataProvider]:
    return [AkShareProvider(), BaostockProvider(), ADataProvider()]


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
