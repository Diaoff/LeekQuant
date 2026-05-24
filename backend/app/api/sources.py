from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.source_service import check_source, check_sources, list_sources, save_sources
from app.data.providers import PROVIDER_REGISTRY
from app.db.session import get_session

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/sources")
async def get_sources(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await list_sources(session)


@router.post("/sources/check")
async def check_all_sources(payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    names = payload.get("names") if payload else None
    if names is not None:
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="names must be a list of source names")
        unknown = [name for name in names if name not in PROVIDER_REGISTRY]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown source name: {unknown[0]}",
            )
    return await check_sources(names)


@router.post("/sources/{source_name}/check")
async def check_one_source(source_name: str) -> dict[str, Any]:
    if source_name not in PROVIDER_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown source name: {source_name}",
        )
    return await check_source(source_name)


@router.put("/sources")
async def update_sources(
    configs: list[dict[str, Any]],
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    for conf in configs:
        if "name" not in conf:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="each source must have a 'name' field")
        if conf["name"] not in PROVIDER_REGISTRY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown source name: {conf['name']}",
            )
    return await save_sources(session, configs)
