from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_source_configs(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, name, display_name, priority, enabled
            FROM data_source_config
            ORDER BY priority ASC
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def replace_source_configs(session: AsyncSession, configs: list[dict[str, Any]]) -> None:
    await session.execute(text("DELETE FROM data_source_config"))
    for idx, conf in enumerate(configs):
        await session.execute(
            text(
                """
                INSERT INTO data_source_config (name, display_name, priority, enabled)
                VALUES (:name, :display_name, :priority, :enabled)
                """
            ),
            {
                "name": conf["name"],
                "display_name": conf.get("display_name", conf["name"]),
                "priority": idx + 1,
                "enabled": conf.get("enabled", True),
            },
        )
    await session.commit()
