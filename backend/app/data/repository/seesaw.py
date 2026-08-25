"""跷跷板避险库数据访问层。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.seesaw import DefensivePoolItem, DefensiveRules, MarketSignalRecord


async def upsert_defensive_pool_item(
    session: AsyncSession,
    ts_code: str,
    name: str,
    *,
    note: str | None = None,
    tags: str | None = None,
    sort_order: int = 0,
) -> int:
    """插入或更新池内股票（按 ts_code 去重）。"""
    result = await session.execute(
        text("SELECT id FROM defensive_pool WHERE ts_code = :ts_code"),
        {"ts_code": ts_code},
    )
    existing = result.fetchone()
    if existing is None:
        await session.execute(
            text(
                """
                INSERT INTO defensive_pool (ts_code, name, note, tags, sort_order, enabled, created_at, updated_at)
                VALUES (:ts_code, :name, :note, :tags, :sort_order, TRUE, NOW(), NOW())
                """
            ),
            {"ts_code": ts_code, "name": name, "note": note, "tags": tags, "sort_order": sort_order},
        )
    else:
        await session.execute(
            text(
                """
                UPDATE defensive_pool SET
                    name = :name,
                    note = :note,
                    tags = :tags,
                    sort_order = :sort_order,
                    updated_at = NOW()
                WHERE ts_code = :ts_code
                """
            ),
            {"ts_code": ts_code, "name": name, "note": note, "tags": tags, "sort_order": sort_order},
        )
    return 1


async def list_defensive_pool(
    session: AsyncSession,
    *,
    enabled_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[DefensivePoolItem]:
    result = await session.execute(
        text(
            """
            SELECT id, ts_code, name, note, tags, sort_order, enabled, created_at, updated_at
            FROM defensive_pool
            WHERE (:enabled_only IS FALSE OR enabled = TRUE)
            ORDER BY sort_order ASC, ts_code ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"enabled_only": enabled_only, "limit": limit, "offset": offset},
    )
    rows = result.mappings().all()
    return [
        DefensivePoolItem(
            id=int(r["id"]),
            ts_code=r["ts_code"],
            name=r["name"],
            note=r["note"],
            tags=r["tags"],
            sort_order=int(r["sort_order"]),
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


async def list_defensive_pool_count(
    session: AsyncSession,
    *,
    enabled_only: bool = True,
) -> int:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM defensive_pool
            WHERE (:enabled_only IS FALSE OR enabled = TRUE)
            """
        ),
        {"enabled_only": enabled_only},
    )
    return int(result.scalar_one())


async def get_defensive_pool_count(
    session: AsyncSession,
    *,
    enabled_only: bool = True,
) -> int:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM defensive_pool
            WHERE (:enabled_only IS FALSE OR enabled = TRUE)
            """
        ),
        {"enabled_only": enabled_only},
    )
    return int(result.scalar_one())


async def update_defensive_pool_item(
    session: AsyncSession,
    item_id: int,
    *,
    name: str | None = None,
    note: str | None = None,
    tags: str | None = None,
    sort_order: int | None = None,
    enabled: bool | None = None,
) -> int:
    sets: list[str] = []
    params: dict[str, Any] = {"id": item_id}
    if name is not None:
        sets.append("name = :name")
        params["name"] = name
    if note is not None:
        sets.append("note = :note")
        params["note"] = note
    if tags is not None:
        sets.append("tags = :tags")
        params["tags"] = tags
    if sort_order is not None:
        sets.append("sort_order = :sort_order")
        params["sort_order"] = sort_order
    if enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = enabled
    if not sets:
        return 0
    await session.execute(
        text(f"UPDATE defensive_pool SET {', '.join(sets)}, updated_at = NOW() WHERE id = :id"),
        params,
    )
    return 1


async def delete_defensive_pool_item(session: AsyncSession, item_id: int) -> int:
    result = await session.execute(
        text("DELETE FROM defensive_pool WHERE id = :id"),
        {"id": item_id},
    )
    return result.rowcount


async def get_defensive_rules(session: AsyncSession) -> DefensiveRules:
    result = await session.execute(
        text(
            """
            SELECT id, index_code, ma_short, ma_long, ma_long2,
                   drop_threshold, high_window, high_drop_pct,
                   vol_expand_thresh, ma_cross_enabled, enabled
            FROM defensive_rules
            ORDER BY id LIMIT 1
            """
        )
    )
    row = result.mappings().one_or_none()
    if row is None:
        return DefensiveRules()
    def _dec(v):
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None
    return DefensiveRules(
        index_code=row["index_code"] or "000300.SH",
        ma_short=int(row["ma_short"]),
        ma_long=int(row["ma_long"]),
        ma_long2=int(row["ma_long2"]),
        drop_threshold=_dec(row["drop_threshold"]) or Decimal("-0.03"),
        high_window=int(row["high_window"]),
        high_drop_pct=_dec(row["high_drop_pct"]) or Decimal("-0.05"),
        vol_expand_thresh=_dec(row["vol_expand_thresh"]),
        ma_cross_enabled=bool(row["ma_cross_enabled"]),
        enabled=bool(row["enabled"]),
    )


async def update_defensive_rules(
    session: AsyncSession,
    rules: DefensiveRules,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO defensive_rules (
                id, index_code, ma_short, ma_long, ma_long2,
                drop_threshold, high_window, high_drop_pct,
                vol_expand_thresh, ma_cross_enabled, enabled, updated_at
            ) VALUES (
                1, :index_code, :ma_short, :ma_long, :ma_long2,
                :drop_threshold, :high_window, :high_drop_pct,
                :vol_expand_thresh, :ma_cross_enabled, :enabled, NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                index_code           = EXCLUDED.index_code,
                ma_short             = EXCLUDED.ma_short,
                ma_long              = EXCLUDED.ma_long,
                ma_long2             = EXCLUDED.ma_long2,
                drop_threshold       = EXCLUDED.drop_threshold,
                high_window          = EXCLUDED.high_window,
                high_drop_pct        = EXCLUDED.high_drop_pct,
                vol_expand_thresh    = EXCLUDED.vol_expand_thresh,
                ma_cross_enabled     = EXCLUDED.ma_cross_enabled,
                enabled              = EXCLUDED.enabled,
                updated_at           = NOW()
            """
        ),
        {
            "index_code": rules.index_code,
            "ma_short": rules.ma_short,
            "ma_long": rules.ma_long,
            "ma_long2": rules.ma_long2,
            "drop_threshold": str(rules.drop_threshold),
            "high_window": rules.high_window,
            "high_drop_pct": str(rules.high_drop_pct),
            "vol_expand_thresh": str(rules.vol_expand_thresh) if rules.vol_expand_thresh else None,
            "ma_cross_enabled": rules.ma_cross_enabled,
            "enabled": rules.enabled,
        },
    )


async def insert_market_signal(
    session: AsyncSession,
    signal: MarketSignalRecord,
) -> int:
    await session.execute(
        text(
            """
            INSERT INTO market_signal_log (
                index_code, state, close_price, prev_close, change_pct,
                ma20_gap, ma60_gap, drop_from_high, condition_detail
            ) VALUES (
                :index_code, :state, :close_price, :prev_close, :change_pct,
                :ma20_gap, :ma60_gap, :drop_from_high, :condition_detail
            )
            """
        ),
        {
            "index_code": signal.index_code,
            "state": signal.state,
            "close_price": str(signal.close_price) if signal.close_price else None,
            "prev_close": str(signal.prev_close) if signal.prev_close else None,
            "change_pct": str(signal.change_pct) if signal.change_pct else None,
            "ma20_gap": str(signal.ma20_gap) if signal.ma20_gap else None,
            "ma60_gap": str(signal.ma60_gap) if signal.ma60_gap else None,
            "drop_from_high": str(signal.drop_from_high) if signal.drop_from_high else None,
            "condition_detail": signal.condition_detail,
        },
    )
    return 1


async def get_latest_market_signal(
    session: AsyncSession,
    index_code: str = "000300.SH",
) -> MarketSignalRecord | None:
    result = await session.execute(
        text(
            """
            SELECT id, index_code, state, trigger_time, close_price, prev_close,
                   change_pct, ma20_gap, ma60_gap, drop_from_high, condition_detail
            FROM market_signal_log
            WHERE index_code = :index_code
            ORDER BY trigger_time DESC
            LIMIT 1
            """
        ),
        {"index_code": index_code},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None

    def _dec(v):
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    return MarketSignalRecord(
        id=int(row["id"]),
        index_code=row["index_code"],
        state=row["state"],
        trigger_time=row["trigger_time"],
        close_price=_dec(row["close_price"]),
        prev_close=_dec(row["prev_close"]),
        change_pct=_dec(row["change_pct"]),
        ma20_gap=_dec(row["ma20_gap"]),
        ma60_gap=_dec(row["ma60_gap"]),
        drop_from_high=_dec(row["drop_from_high"]),
        condition_detail=row["condition_detail"] or {},
    )


async def insert_seesaw_trigger(
    session: AsyncSession,
    market_state: str,
    index_code: str,
    recommendations: list[dict[str, Any]],
) -> int:
    await session.execute(
        text(
            """
            INSERT INTO seesaw_trigger_log (
                market_state, index_code, recommended_count, recommendations
            ) VALUES (
                :market_state, :index_code, :recommended_count, :recommendations
            )
            """
        ),
        {
            "market_state": market_state,
            "index_code": index_code,
            "recommended_count": len(recommendations),
            "recommendations": str(recommendations).replace("'", "\""),
        },
    )
    return 1


async def list_seesaw_triggers(
    session: AsyncSession,
    *,
    market_state: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if market_state:
        clauses.append("market_state = :market_state")
        params["market_state"] = market_state
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    result = await session.execute(
        text(
            f"""
            SELECT id, trigger_time, market_state, index_code,
                   recommended_count, recommendations, subsequent_perf, created_at
            FROM seesaw_trigger_log
            {where}
            ORDER BY trigger_time DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


async def update_seesaw_trigger_perf(
    session: AsyncSession,
    trigger_id: int,
    perf: dict[str, Any],
) -> int:
    result = await session.execute(
        text(
            """
            UPDATE seesaw_trigger_log
            SET subsequent_perf = CAST(:perf AS JSONB), updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"perf": str(perf).replace("'", "\""), "id": trigger_id},
    )
    return result.rowcount

async def get_cached_beta_list(
    session: AsyncSession,
    ts_codes: list[str],
) -> dict[str, Decimal | None]:
    """批量读取 β 缓存，返回 ts_code -> beta 字典。"""
    if not ts_codes:
        return {}
    result = await session.execute(
        text(
            """
            SELECT ts_code, beta_value
            FROM beta_cached
            WHERE ts_code = ANY(:ts_codes)
              AND calculated_date >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY ts_code, calculated_date DESC
            """
        ),
        {"ts_codes": ts_codes},
    )
    seen: set[str] = set()
    out: dict[str, Decimal] = {}
    for row in result.mappings().all():
        code = row["ts_code"]
        if code in seen:
            continue
        seen.add(code)
        try:
            out[code] = Decimal(str(row["beta_value"]))
        except Exception:
            out[code] = None
    # Codes not in cache get None
    return out

