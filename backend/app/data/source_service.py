from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fetcher import configure_providers
from app.data.source_repository import get_source_configs, replace_source_configs
from app.data.providers import PROVIDER_REGISTRY


async def list_sources(session: AsyncSession) -> list[dict[str, Any]]:
    return await get_source_configs(session)


async def save_sources(session: AsyncSession, configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    await replace_source_configs(session, configs)
    enabled = [c["name"] for c in configs if c.get("enabled", True)]
    configure_providers(enabled)
    return await get_source_configs(session)


async def apply_config_from_db(session: AsyncSession) -> None:
    configs = await get_source_configs(session)
    if not configs:
        configure_providers(list(PROVIDER_REGISTRY.keys()))
        return
    enabled = [c["name"] for c in configs if c["enabled"]]
    configure_providers(enabled)
