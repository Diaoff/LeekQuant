from __future__ import annotations

from collections.abc import Iterable
from typing import TypeAlias

from app.data.providers import (
    ADataProvider,
    AkShareProvider,
    BaostockProvider,
    DataProvider,
    DataProviderError,
)

__all__ = ["DataProvider", "DataProviderError", "default_providers", "fetch_with_fallback"]

ProviderList: TypeAlias = Iterable[DataProvider]


def default_providers() -> list[DataProvider]:
    return [ADataProvider(), BaostockProvider(), AkShareProvider()]


def fetch_with_fallback(
    providers: ProviderList,
    method_name: str,
    *args,
) -> tuple[str, list]:
    errors: list[str] = []
    for provider in providers:
        method = getattr(provider, method_name)
        try:
            records = method(*args)
        except Exception as exc:
            errors.append(f"{provider.name}: {exc}")
            continue
        if records:
            return provider.name, records
        errors.append(f"{provider.name}: no records returned")
    raise DataProviderError("; ".join(errors) or "all providers failed")
