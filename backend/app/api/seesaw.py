"""跷跷板效应（高切低）避险库 API。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.repository.seesaw import (
    delete_defensive_pool_item,
    get_defensive_rules,
    list_defensive_pool,
    list_defensive_pool_count,
    update_defensive_pool_item,
    update_defensive_rules,
)
from app.data.seesaw import DefensiveRules, detect_market_state, get_seesaw_recommendations
from app.db.session import get_session

router = APIRouter(prefix="/api/seesaw", tags=["seesaw"])


# ── 请求模型 ──────────────────────────────────────────────────────────────────


class AddPoolItemRequest(BaseModel):
    ts_code: str = Field(..., min_length=6, max_length=10, description="股票代码，如 600519.SH")
    name: str = Field(..., min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=256)
    tags: str | None = Field(default=None, max_length=256)
    sort_order: int = Field(default=0)


class UpdatePoolItemRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=256)
    tags: str | None = Field(default=None, max_length=256)
    sort_order: int | None = None
    enabled: bool | None = None


class UpdateRulesRequest(BaseModel):
    index_code: str | None = None
    ma_short: int | None = Field(default=None, ge=2, le=60)
    ma_long: int | None = Field(default=None, ge=5, le=200)
    ma_long2: int | None = Field(default=None, ge=20, le=250)
    drop_threshold: float | None = Field(default=None, le=-0.001)
    high_window: int | None = Field(default=None, ge=5, le=120)
    high_drop_pct: float | None = Field(default=None, le=-0.01)
    vol_expand_thresh: float | None = Field(default=None, gt=0)
    ma_cross_enabled: bool | None = None
    enabled: bool | None = None


class UpdatePerfRequest(BaseModel):
    subsequent_perf: dict[str, Any]


# ── 池管理 ─────────────────────────────────────────────────────────────────────


@router.get("/pool")
async def list_pool(
    enabled_only: bool = Query(default=True, description="仅返回启用项"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    total = await list_defensive_pool_count(session, enabled_only=enabled_only)
    items = await list_defensive_pool(session, enabled_only=enabled_only, limit=page_size, offset=(page - 1) * page_size)
    return {
        "items": [
            {
                "id": it.id,
                "ts_code": it.ts_code,
                "name": it.name,
                "note": it.note,
                "tags": it.tags,
                "sort_order": it.sort_order,
                "enabled": it.enabled,
                "created_at": str(it.created_at) if it.created_at else None,
                "updated_at": str(it.updated_at) if it.updated_at else None,
            }
            for it in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/pool")
async def add_pool_item(
    request: AddPoolItemRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.data.repository.seesaw import upsert_defensive_pool_item
    await upsert_defensive_pool_item(
        session,
        request.ts_code,
        request.name,
        note=request.note,
        tags=request.tags,
        sort_order=request.sort_order,
    )
    await session.commit()
    return {"ok": True, "ts_code": request.ts_code}


@router.put("/pool/{item_id}")
async def update_pool_item(
    item_id: int,
    request: UpdatePoolItemRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.data.repository.seesaw import update_defensive_pool_item
    affected = await update_defensive_pool_item(
        session, item_id,
        name=request.name,
        note=request.note,
        tags=request.tags,
        sort_order=request.sort_order,
        enabled=request.enabled,
    )
    await session.commit()
    if affected == 0:
        raise HTTPException(status_code=404, detail="pool item not found")
    return {"ok": True, "item_id": item_id}


@router.delete("/pool/{item_id}")
async def delete_pool_item(
    item_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.data.repository.seesaw import delete_defensive_pool_item
    affected = await delete_defensive_pool_item(session, item_id)
    await session.commit()
    if affected == 0:
        raise HTTPException(status_code=404, detail="pool item not found")
    return {"ok": True, "item_id": item_id}


# ── 市场状态 ────────────────────────────────────────────────────────────────────


@router.get("/market-state")
async def get_market_state(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rules = await get_defensive_rules(session)
    state, detail = await detect_market_state(session, rules)
    return {
        "index_code": rules.index_code,
        "state": state,
        "detail": detail,
        "rules": {
            "ma_short": rules.ma_short,
            "ma_long": rules.ma_long,
            "ma_long2": rules.ma_long2,
            "drop_threshold": float(rules.drop_threshold),
            "high_window": rules.high_window,
            "high_drop_pct": float(rules.high_drop_pct),
        },
    }


# ── 推荐 ────────────────────────────────────────────────────────────────────────


@router.get("/recommend")
async def get_recommend(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rules = await get_defensive_rules(session)
    state, detail = await detect_market_state(session, rules)
    recs = await get_seesaw_recommendations(session, state, rules, limit=20)
    return {
        "market_state": state,
        "detail": detail,
        "recommendations": [
            {
                "ts_code": r.ts_code,
                "name": r.name,
                "score": r.score,
                "beta": r.beta,
                "dividend_yield": r.dividend_yield,
                "pe_ttm": r.pe_ttm,
                "reason": r.reason,
            }
            for r in recs
        ],
    }


# ── 规则配置 ────────────────────────────────────────────────────────────────────


@router.get("/rules")
async def get_rules(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rules = await get_defensive_rules(session)
    return _rules_to_dict(rules)


@router.put("/rules")
async def update_rules(
    request: UpdateRulesRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rules = await get_defensive_rules(session)
    if request.index_code is not None:
        rules.index_code = request.index_code
    if request.ma_short is not None:
        rules.ma_short = request.ma_short
    if request.ma_long is not None:
        rules.ma_long = request.ma_long
    if request.ma_long2 is not None:
        rules.ma_long2 = request.ma_long2
    if request.drop_threshold is not None:
        rules.drop_threshold = Decimal(str(request.drop_threshold))
    if request.high_window is not None:
        rules.high_window = request.high_window
    if request.high_drop_pct is not None:
        rules.high_drop_pct = Decimal(str(request.high_drop_pct))
    if request.vol_expand_thresh is not None:
        rules.vol_expand_thresh = Decimal(str(request.vol_expand_thresh))
    if request.ma_cross_enabled is not None:
        rules.ma_cross_enabled = request.ma_cross_enabled
    if request.enabled is not None:
        rules.enabled = request.enabled
    from app.data.repository.seesaw import update_defensive_rules
    await update_defensive_rules(session, rules)
    await session.commit()
    return _rules_to_dict(rules)


def _rules_to_dict(rules: DefensiveRules) -> dict[str, Any]:
    return {
        "index_code": rules.index_code,
        "ma_short": rules.ma_short,
        "ma_long": rules.ma_long,
        "ma_long2": rules.ma_long2,
        "drop_threshold": float(rules.drop_threshold),
        "high_window": rules.high_window,
        "high_drop_pct": float(rules.high_drop_pct),
        "vol_expand_thresh": float(rules.vol_expand_thresh) if rules.vol_expand_thresh else None,
        "ma_cross_enabled": rules.ma_cross_enabled,
        "enabled": rules.enabled,
    }


# ── 触发历史 ────────────────────────────────────────────────────────────────────


@router.get("/triggers")
async def list_triggers(
    market_state: str | None = Query(default=None, description="过滤状态：down/up/neutral"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.data.repository.seesaw import list_seesaw_triggers
    triggers = await list_seesaw_triggers(
        session, market_state=market_state, limit=page_size, offset=(page - 1) * page_size
    )
    # Count total
    clauses = ["1=1"]
    params: dict[str, Any] = {}
    if market_state:
        clauses.append("market_state = :market_state")
        params["market_state"] = market_state
    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM seesaw_trigger_log WHERE {' AND '.join(clauses)}"),
        params,
    )
    total = int(count_result.scalar_one())
    return {
        "items": triggers,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/triggers/{trigger_id}/perf")
async def update_trigger_perf(
    trigger_id: int,
    request: UpdatePerfRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.data.repository.seesaw import update_seesaw_trigger_perf
    affected = await update_seesaw_trigger_perf(session, trigger_id, request.subsequent_perf)
    await session.commit()
    if affected == 0:
        raise HTTPException(status_code=404, detail="trigger not found")
    return {"ok": True, "trigger_id": trigger_id}
