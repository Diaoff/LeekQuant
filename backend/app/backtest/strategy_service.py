"""Strategy CRUD service."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.stock_service import LOCAL_USER_ID


async def list_strategies(
    session: AsyncSession,
    user_id: int = LOCAL_USER_ID,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["s.user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id}
    if status:
        clauses.append("s.status = :status")
        params["status"] = status

    result = await session.execute(
        text(
            f"""
            SELECT s.id, s.name, s.description, s.pool_id, s.status,
                   s.version, s.config, s.created_at, s.updated_at, s.archived_at,
                   p.name AS pool_name
            FROM strategies s
            LEFT JOIN stock_pools p ON p.id = s.pool_id
            WHERE {" AND ".join(clauses)}
            ORDER BY s.updated_at DESC
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


async def get_strategy(
    session: AsyncSession,
    strategy_id: int,
    user_id: int = LOCAL_USER_ID,
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT s.id, s.user_id, s.name, s.description, s.source_code,
                   s.pool_id, s.status, s.version, s.config,
                   s.created_at, s.updated_at, s.archived_at,
                   p.name AS pool_name
            FROM strategies s
            LEFT JOIN stock_pools p ON p.id = s.pool_id
            WHERE s.id = :id AND s.user_id = :user_id
            """
        ),
        {"id": strategy_id, "user_id": user_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def create_strategy(
    session: AsyncSession,
    *,
    name: str,
    source_code: str,
    description: str | None = None,
    pool_id: int | None = None,
    config: dict[str, Any] | None = None,
    status: str = "draft",
    user_id: int = LOCAL_USER_ID,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            INSERT INTO strategies (user_id, name, description, source_code, pool_id, config, status)
            VALUES (:user_id, :name, :description, :source_code, :pool_id,
                    CAST(:config AS JSONB), :status)
            RETURNING id, name, description, source_code, pool_id, status,
                      version, config, created_at, updated_at, archived_at
            """
        ),
        {
            "user_id": user_id,
            "name": name,
            "description": description,
            "source_code": source_code,
            "pool_id": pool_id,
            "config": json.dumps(config or {}, ensure_ascii=False, default=str),
            "status": status,
        },
    )
    await session.commit()
    return dict(result.mappings().one())


async def update_strategy(
    session: AsyncSession,
    strategy_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    source_code: str | None = None,
    pool_id: int | None = None,
    config: dict[str, Any] | None = None,
    status: str | None = None,
    user_id: int = LOCAL_USER_ID,
) -> dict[str, Any] | None:
    current = await get_strategy(session, strategy_id, user_id)
    if current is None:
        return None

    updates = []
    params: dict[str, Any] = {"id": strategy_id, "user_id": user_id}

    if name is not None:
        updates.append("name = :name")
        params["name"] = name
    if description is not None:
        updates.append("description = :description")
        params["description"] = description
    if source_code is not None:
        updates.append("source_code = :source_code")
        updates.append("version = version + 1")
        params["source_code"] = source_code
    if pool_id is not None:
        updates.append("pool_id = :pool_id")
        params["pool_id"] = pool_id
    if config is not None:
        updates.append("config = CAST(:config AS JSONB)")
        params["config"] = json.dumps(config, ensure_ascii=False, default=str)
    if status is not None:
        updates.append("status = :status")
        params["status"] = status
        if status == "archived":
            updates.append("archived_at = NOW()")

    if not updates:
        return current

    result = await session.execute(
        text(
            f"""
            UPDATE strategies
            SET {", ".join(updates)},
                updated_at = NOW()
            WHERE id = :id AND user_id = :user_id
            RETURNING id, name, description, source_code, pool_id, status,
                      version, config, created_at, updated_at, archived_at
            """
        ),
        params,
    )
    await session.commit()
    return dict(result.mappings().one())


async def delete_strategy(
    session: AsyncSession,
    strategy_id: int,
    user_id: int = LOCAL_USER_ID,
) -> bool:
    result = await session.execute(
        text("DELETE FROM strategies WHERE id = :id AND user_id = :user_id"),
        {"id": strategy_id, "user_id": user_id},
    )
    await session.commit()
    return result.rowcount > 0
