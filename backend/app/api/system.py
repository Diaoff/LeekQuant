from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repository import list_alerts
from app.db.session import get_session

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/alerts")
async def system_alerts(
    level: str | None = Query(default=None),
    category: str | None = Query(default=None),
    is_resolved: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_alerts(
        session,
        level=level,
        category=category,
        is_resolved=is_resolved,
        limit=limit,
        offset=offset,
    )
