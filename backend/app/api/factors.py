"""Factor definition, ranking, values, and IC/IR query API."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.factor.service import (
    create_factor_definition,
    delete_factor_definition,
    list_factor_definitions,
    query_factor_analysis,
    query_factor_values,
    query_rank,
    update_factor_definition,
    validate_factor_expression,
)

router = APIRouter(prefix="/api/factors", tags=["factors"])


class FactorCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    display_name: str | None = Field(default=None, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    expression: str = Field(min_length=1, max_length=1024)
    direction: int = Field(default=1, ge=-1, le=1)
    default_weight: float = Field(default=1.0, gt=0, le=100)
    description: str | None = Field(default=None, max_length=512)


class FactorUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    expression: str | None = Field(default=None, min_length=1, max_length=1024)
    direction: int | None = Field(default=None, ge=-1, le=1)
    default_weight: float | None = Field(default=None, gt=0, le=100)
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=512)


class ValidateRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=1024)


@router.get("")
async def get_factors(
    enabled_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_factor_definitions(session, enabled_only=enabled_only)


@router.post("/validate")
async def post_validate_expression(body: ValidateRequest) -> dict[str, Any]:
    is_valid, error = validate_factor_expression(body.expression)
    return {"valid": is_valid, "error": error}


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_factor(
    body: FactorCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    is_valid, error = validate_factor_expression(body.expression)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid expression: {error}",
        )
    try:
        return await create_factor_definition(
            session,
            name=body.name,
            display_name=body.display_name or body.name,
            category=body.category,
            expression=body.expression,
            direction=body.direction,
            default_weight=body.default_weight,
            description=body.description or "",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )


@router.put("/{name}")
async def put_factor(
    name: str,
    body: FactorUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if body.expression is not None:
        is_valid, error = validate_factor_expression(body.expression)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid expression: {error}",
            )
    try:
        result = await update_factor_definition(
            session,
            name=name,
            display_name=body.display_name,
            category=body.category,
            expression=body.expression,
            direction=body.direction,
            default_weight=body.default_weight,
            enabled=body.enabled,
            description=body.description,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return result


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_factor(
    name: str,
    session: AsyncSession = Depends(get_session),
):
    try:
        await delete_factor_definition(session, name=name)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/rank")
async def get_factor_rank(
    trade_date: date | None = None,
    scope_type: str = Query(default="all", pattern="^(all|watchlist_group)$"),
    scope_value: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if scope_type == "watchlist_group" and not scope_value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scope_value is required for watchlist_group scope",
        )
    return await query_rank(
        session,
        trade_date=trade_date,
        scope_type=scope_type,
        scope_value=scope_value,
        page=page,
        page_size=page_size,
    )


@router.get("/analysis")
async def get_factor_analysis(
    factor_name: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await query_factor_analysis(
        session,
        factor_name=factor_name,
        page=page,
        page_size=page_size,
    )


@router.get("/values")
async def get_factor_values(
    trade_date: date,
    factor_name: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await query_factor_values(
        session,
        trade_date=trade_date,
        factor_name=factor_name,
        page=page,
        page_size=page_size,
    )
