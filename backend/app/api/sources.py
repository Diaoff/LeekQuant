from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.source_service import list_sources, save_sources
from app.db.session import get_session

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/sources")
async def get_sources(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await list_sources(session)


@router.put("/sources")
async def update_sources(
    configs: list[dict[str, Any]],
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    for conf in configs:
        if "name" not in conf:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="each source must have a 'name' field")
        if conf["name"] not in ("adata", "baostock", "akshare"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown source name: {conf['name']}",
            )
    return await save_sources(session, configs)
