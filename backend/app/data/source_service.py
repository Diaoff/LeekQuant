from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fetcher import configure_providers
from app.data.source_repository import get_source_configs, replace_source_configs
from app.data.providers import METHOD_CAPABILITIES, PROVIDER_REGISTRY, provider_metadata, provider_supports

CHECK_METHODS = [
    ("fetch_daily_kline", ("000001.SZ",)),
    ("fetch_stock_fundamentals", (["000001.SZ"],)),
    ("fetch_trade_calendar", ()),
    ("fetch_stock_basic", ()),
]


async def list_sources(session: AsyncSession) -> list[dict[str, Any]]:
    return _merge_registered_sources(await get_source_configs(session))


async def save_sources(session: AsyncSession, configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    registered = set(PROVIDER_REGISTRY)
    filtered = [c for c in configs if c["name"] in registered]
    await replace_source_configs(session, filtered)
    enabled = [c["name"] for c in filtered if c.get("enabled", True)]
    configure_providers(enabled)
    return await list_sources(session)


async def apply_config_from_db(session: AsyncSession) -> None:
    configs = _merge_registered_sources(await get_source_configs(session))
    if not configs:
        configure_providers([c["name"] for c in provider_metadata()])
        return
    enabled = [c["name"] for c in configs if c["enabled"]]
    configure_providers(enabled)


async def check_source(name: str) -> dict[str, Any]:
    provider_cls = PROVIDER_REGISTRY.get(name)
    checked_at = datetime.now(tz=UTC).isoformat()
    if provider_cls is None:
        return {
            "name": name,
            "ok": False,
            "checked_capability": None,
            "records": 0,
            "latency_ms": 0,
            "checked_at": checked_at,
            "error": f"unknown source name: {name}",
        }

    provider = provider_cls()
    start_date, end_date = _check_window()
    errors: list[str] = []
    started = time.perf_counter()

    for method_name, prefix_args in CHECK_METHODS:
        capability = METHOD_CAPABILITIES[method_name]
        if not provider_supports(provider, capability):
            continue
        method = getattr(provider, method_name)
        args = _check_args(method_name, prefix_args, start_date, end_date)
        try:
            records = method(*args)
        except Exception as exc:
            errors.append(f"{capability}: {exc}")
            continue

        count = len(records) if records is not None else 0
        latency_ms = int((time.perf_counter() - started) * 1000)
        if count > 0:
            return {
                "name": name,
                "display_name": getattr(provider, "display_name", name),
                "ok": True,
                "checked_capability": capability,
                "records": count,
                "latency_ms": latency_ms,
                "checked_at": checked_at,
                "error": None,
            }
        errors.append(f"{capability}: no records returned")

    return {
        "name": name,
        "display_name": getattr(provider, "display_name", name),
        "ok": False,
        "checked_capability": None,
        "records": 0,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "checked_at": checked_at,
        "error": "; ".join(errors) or "source has no checkable capabilities",
    }


async def check_sources(names: list[str] | None = None) -> list[dict[str, Any]]:
    checked_names = names or [item["name"] for item in provider_metadata()]
    return [await check_source(name) for name in checked_names]


def _merge_registered_sources(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    registered = {item["name"]: item for item in provider_metadata()}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for idx, config in enumerate(configs, start=1):
        name = config["name"]
        metadata = registered.get(name)
        if metadata is None:
            continue
        item = {
            **metadata,
            **config,
            "priority": config.get("priority", idx),
            "capabilities": metadata["capabilities"],
        }
        merged.append(item)
        seen.add(name)

    next_priority = len(merged) + 1
    for metadata in sorted(registered.values(), key=lambda item: item["priority"]):
        if metadata["name"] in seen:
            continue
        merged.append({**metadata, "priority": next_priority})
        next_priority += 1

    return sorted(merged, key=lambda item: item["priority"])


def _check_window() -> tuple[date, date]:
    today = datetime.now(tz=UTC).date()
    return today - timedelta(days=14), today


def _check_args(method_name: str, prefix_args: tuple[Any, ...], start_date: date, end_date: date) -> tuple[Any, ...]:
    if method_name == "fetch_stock_basic":
        return ()
    return (*prefix_args, start_date, end_date)
