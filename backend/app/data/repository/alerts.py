from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_alert(
    session: AsyncSession,
    *,
    level: str,
    category: str,
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO alert_events (level, category, title, message, payload)
            VALUES (:level, :category, :title, :message, CAST(:payload AS JSONB))
            """
        ),
        {
            "level": level,
            "category": category,
            "title": title,
            "message": message,
            "payload": json.dumps(payload or {}, ensure_ascii=False, default=str),
        },
    )


async def list_alerts(
    session: AsyncSession,
    *,
    level: str | None = None,
    category: str | None = None,
    is_resolved: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    filters = []
    params: dict[str, Any] = {
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }
    if level is not None:
        filters.append("level = :level")
        params["level"] = level
    if category is not None:
        filters.append("category = :category")
        params["category"] = category
    if is_resolved is not None:
        filters.append("is_resolved = :is_resolved")
        params["is_resolved"] = is_resolved

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    result = await session.execute(
        text(
            f"""
            SELECT id, level, category, title, message, payload, is_resolved, created_at, resolved_at
            FROM alert_events
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


async def resolve_alert(session: AsyncSession, alert_id: int) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            UPDATE alert_events
            SET is_resolved = TRUE,
                resolved_at = NOW()
            WHERE id = :alert_id
            RETURNING id, level, category, title, message, payload, is_resolved, created_at, resolved_at
            """
        ),
        {"alert_id": alert_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    await session.commit()
    return dict(row)
