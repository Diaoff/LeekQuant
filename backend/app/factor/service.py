from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.factor.definitions import BUILTIN_FACTORS
from app.factor.expression import FactorContext, evaluate_expression, validate_expression as _validate_expression
from app.libs import MyTT

FACTOR_QUANT = Decimal("0.00000001")
SCORE_QUANT = Decimal("0.00000001")
BUILTIN_FACTOR_NAMES = {factor.name for factor in BUILTIN_FACTORS}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _to_decimal(value: Any) -> Decimal | None:
    number = _to_float(value)
    if number is None:
        return None
    if abs(number) < 0.000000005:
        number = 0.0
    return Decimal(str(number)).quantize(FACTOR_QUANT, rounding=ROUND_HALF_UP)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _serialize_value(value) for key, value in dict(row).items()} for row in rows]


def normalize_cross_section(values: dict[str, Any], direction: int) -> dict[str, dict[str, Decimal]]:
    cleaned = {code: _to_float(value) for code, value in values.items()}
    series = pd.Series({code: value for code, value in cleaned.items() if value is not None}, dtype="float64")
    if series.empty:
        return {}

    lower = float(series.quantile(0.01))
    upper = float(series.quantile(0.99))
    clipped = series.clip(lower=lower, upper=upper)
    std = float(clipped.std(ddof=0))
    if std == 0 or not isfinite(std):
        normalized = clipped * 0
    else:
        normalized = (clipped - float(clipped.mean())) / std
    normalized = normalized * (1 if direction >= 0 else -1)
    percentiles = normalized.rank(pct=True, method="average")

    result: dict[str, dict[str, Decimal]] = {}
    for ts_code in normalized.index:
        norm_value = float(normalized.loc[ts_code])
        pct_value = float(percentiles.loc[ts_code])
        result[str(ts_code)] = {
            "normalized_value": Decimal(str(norm_value)).quantize(FACTOR_QUANT, rounding=ROUND_HALF_UP),
            "percentile_rank": Decimal(str(pct_value)).quantize(FACTOR_QUANT, rounding=ROUND_HALF_UP),
        }
    return result


async def seed_factor_definitions(session: AsyncSession, *, commit: bool = True) -> int:
    rows = [
        {
            "name": factor.name,
            "display_name": factor.display_name,
            "category": factor.category,
            "expression": factor.expression,
            "direction": factor.direction,
            "default_weight": factor.default_weight,
            "enabled": factor.enabled,
            "description": factor.description,
        }
        for factor in BUILTIN_FACTORS
    ]
    await session.execute(
        text(
            """
            INSERT INTO factor_definitions (
                name, display_name, category, expression, direction,
                default_weight, enabled, description
            )
            VALUES (
                :name, :display_name, :category, :expression, :direction,
                :default_weight, :enabled, :description
            )
            ON CONFLICT (name) DO NOTHING
            """
        ),
        rows,
    )
    if commit:
        await session.commit()
    return len(rows)


async def list_factor_definitions(session: AsyncSession, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    clauses = ["TRUE"]
    if enabled_only:
        clauses.append("enabled = TRUE")
    result = await session.execute(
        text(
            f"""
            SELECT name, display_name, category, expression, direction,
                   default_weight, enabled, description, created_at, updated_at
            FROM factor_definitions
            WHERE {" AND ".join(clauses)}
            ORDER BY category, name
            """
        )
    )
    return serialize_rows(list(result.mappings().all()))


def validate_factor_expression(expr: str) -> tuple[bool, str | None]:
    return _validate_expression(expr)


async def create_factor_definition(
    session: AsyncSession,
    *,
    name: str,
    display_name: str,
    category: str,
    expression: str,
    direction: int,
    default_weight: float,
    description: str,
) -> dict[str, Any]:
    existing = await session.execute(
        text("SELECT name FROM factor_definitions WHERE name = :name"),
        {"name": name},
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"factor '{name}' already exists")

    await session.execute(
        text(
            """
            INSERT INTO factor_definitions (
                name, display_name, category, expression, direction,
                default_weight, enabled, description
            ) VALUES (
                :name, :display_name, :category, :expression, :direction,
                :default_weight, TRUE, :description
            )
            """
        ),
        {
            "name": name,
            "display_name": display_name,
            "category": category,
            "expression": expression,
            "direction": direction,
            "default_weight": default_weight,
            "description": description,
        },
    )
    await session.commit()
    result = await session.execute(
        text(
            """
            SELECT name, display_name, category, expression, direction,
                   default_weight, enabled, description, created_at, updated_at
            FROM factor_definitions WHERE name = :name
            """
        ),
        {"name": name},
    )
    return dict(result.mappings().one())


async def update_factor_definition(
    session: AsyncSession,
    *,
    name: str,
    display_name: str | None = None,
    category: str | None = None,
    expression: str | None = None,
    direction: int | None = None,
    default_weight: float | None = None,
    enabled: bool | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    existing = await session.execute(
        text("SELECT name FROM factor_definitions WHERE name = :name"),
        {"name": name},
    )
    if existing.scalar_one_or_none() is None:
        raise KeyError(f"factor '{name}' not found")

    sets: list[str] = []
    params: dict[str, Any] = {"name": name}
    if display_name is not None:
        sets.append("display_name = :display_name")
        params["display_name"] = display_name
    if category is not None:
        sets.append("category = :category")
        params["category"] = category
    if expression is not None:
        sets.append("expression = :expression")
        params["expression"] = expression
    if direction is not None:
        sets.append("direction = :direction")
        params["direction"] = direction
    if default_weight is not None:
        sets.append("default_weight = :default_weight")
        params["default_weight"] = default_weight
    if enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = enabled
    if description is not None:
        sets.append("description = :description")
        params["description"] = description

    if not sets:
        raise ValueError("no fields to update")

    sets.append("updated_at = NOW()")
    await session.execute(
        text(f"UPDATE factor_definitions SET {', '.join(sets)} WHERE name = :name"),
        params,
    )
    await session.commit()

    result = await session.execute(
        text(
            """
            SELECT name, display_name, category, expression, direction,
                   default_weight, enabled, description, created_at, updated_at
            FROM factor_definitions WHERE name = :name
            """
        ),
        {"name": name},
    )
    return dict(result.mappings().one())


async def delete_factor_definition(session: AsyncSession, *, name: str) -> None:
    if name in BUILTIN_FACTOR_NAMES:
        raise ValueError(f"cannot delete builtin factor '{name}'")

    existing = await session.execute(
        text("SELECT name FROM factor_definitions WHERE name = :name"),
        {"name": name},
    )
    if existing.scalar_one_or_none() is None:
        raise KeyError(f"factor '{name}' not found")

    await session.execute(
        text("DELETE FROM factor_definitions WHERE name = :name"),
        {"name": name},
    )
    await session.commit()


async def _enabled_factor_definitions(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT name, display_name, category, expression, direction, default_weight, enabled, description
            FROM factor_definitions
            WHERE enabled = TRUE AND default_weight > 0
            ORDER BY name
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def _last_open_trade_date(session: AsyncSession, requested: date | None) -> date | None:
    if requested is not None:
        result = await session.execute(
            text(
                """
                SELECT is_open
                FROM trade_calendar
                WHERE cal_date = :requested
                """
            ),
            {"requested": requested},
        )
        is_open = result.scalar_one_or_none()
        return requested if is_open is True else None
    result = await session.execute(
        text(
            """
            SELECT cal_date
            FROM trade_calendar
            WHERE is_open = TRUE AND cal_date <= CURRENT_DATE
            ORDER BY cal_date DESC
            LIMIT 1
            """
        )
    )
    value = result.scalar_one_or_none()
    return _date(value) if value is not None else None


async def _latest_fundamentals(session: AsyncSession, trade_date: date) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (sf.ts_code)
                   sf.ts_code, sf.pe_ttm, sf.pb, sf.roe, sf.revenue_growth
            FROM stock_fundamentals sf
            JOIN stock_basic sb ON sb.ts_code = sf.ts_code
            WHERE sf.report_date <= :trade_date
              AND sb.is_delisted = FALSE
            ORDER BY sf.ts_code, sf.report_date DESC
            """
        ),
        {"trade_date": trade_date},
    )
    return {row["ts_code"]: dict(row) for row in result.mappings().all()}


async def _recent_kline_rows(session: AsyncSession, trade_date: date, lookback: int = 80) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            WITH ranked AS (
                SELECT dk.ts_code, dk.trade_date, dk.close, dk.volume,
                       ROW_NUMBER() OVER (PARTITION BY dk.ts_code ORDER BY dk.trade_date DESC) AS rn
                FROM daily_kline dk
                JOIN stock_basic sb ON sb.ts_code = dk.ts_code
                WHERE dk.trade_date <= :trade_date
                  AND dk.close IS NOT NULL
                  AND dk.is_suspended = FALSE
                  AND sb.is_delisted = FALSE
            )
            SELECT ts_code, trade_date, close, volume
            FROM ranked
            WHERE rn <= :lookback
            ORDER BY ts_code, trade_date
            """
        ),
        {"trade_date": trade_date, "lookback": lookback},
    )
    return [dict(row) for row in result.mappings().all()]


def _compute_raw_factor_values(
    fundamentals: dict[str, dict[str, Any]],
    kline_rows: list[dict[str, Any]],
    definitions: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Decimal]]:
    """Compute raw factor values for all stocks using expression-driven evaluation.

    When definitions are provided, evaluates each factor's expression string.
    Falls back to hardcoded builtin computation for backward compatibility
    when no definitions are passed.
    """
    raw: dict[str, dict[str, Decimal]] = defaultdict(dict)

    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in kline_rows:
        by_code[row["ts_code"]].append(row)

    ts_codes = sorted(set(by_code.keys()) | set(fundamentals.keys()))

    if definitions is not None:
        for defn in definitions:
            factor_name = defn["name"]
            expression = defn.get("expression", "")
            if not expression:
                continue
            for ts_code in ts_codes:
                rows = by_code.get(ts_code, [])
                if not rows:
                    continue
                closes = np.array([_to_float(r.get("close")) or np.nan for r in rows], dtype=np.float64)
                opens = np.array([_to_float(r.get("open")) or np.nan for r in rows], dtype=np.float64)
                highs = np.array([_to_float(r.get("high")) or np.nan for r in rows], dtype=np.float64)
                lows = np.array([_to_float(r.get("low")) or np.nan for r in rows], dtype=np.float64)
                volumes = np.array([float(r.get("volume") or 0) for r in rows], dtype=np.float64)
                amounts = np.array([_to_float(r.get("amount")) or 0.0 for r in rows], dtype=np.float64)

                fund = fundamentals.get(ts_code, {})
                ctx = FactorContext(
                    kline={
                        "$close": closes,
                        "$open": opens,
                        "$high": highs,
                        "$low": lows,
                        "$volume": volumes,
                        "$amount": amounts,
                    },
                    fundamentals={
                        "pe_ttm": _to_float(fund.get("pe_ttm")),
                        "pb": _to_float(fund.get("pb")),
                        "roe": _to_float(fund.get("roe")),
                        "revenue_growth": _to_float(fund.get("revenue_growth")),
                    },
                    length=len(rows),
                )
                try:
                    result = evaluate_expression(expression, ctx)
                    last_val = float(result[-1]) if len(result) > 0 else np.nan
                    if np.isfinite(last_val):
                        raw[factor_name][ts_code] = _to_decimal(last_val)
                except Exception:
                    continue
        return raw

    for ts_code, row in fundamentals.items():
        for factor_name in ("pe_ttm", "pb", "roe", "revenue_growth"):
            value = _to_decimal(row.get(factor_name))
            if value is not None:
                raw[factor_name][ts_code] = value

    for ts_code, rows in by_code.items():
        closes = [_to_float(row.get("close")) for row in rows]
        closes = [value for value in closes if value is not None]
        if len(closes) < 2:
            continue
        close_series = pd.Series(closes, dtype="float64")
        latest = float(close_series.iloc[-1])
        if len(close_series) >= 21 and close_series.iloc[-21] != 0:
            raw["mom_20d"][ts_code] = _to_decimal(latest / float(close_series.iloc[-21]) - 1)
            returns_20 = close_series.pct_change().dropna().tail(20)
            if len(returns_20) >= 20:
                raw["vol_20d"][ts_code] = _to_decimal(float(returns_20.std(ddof=0)))
        if len(close_series) >= 61 and close_series.iloc[-61] != 0:
            raw["mom_60d"][ts_code] = _to_decimal(latest / float(close_series.iloc[-61]) - 1)
        if len(close_series) >= 7:
            rsi_values = MyTT.RSI(close_series.to_numpy(), 6)
            raw["rsi6"][ts_code] = _to_decimal(rsi_values[-1])

    return raw


async def _upsert_factor_values(
    session: AsyncSession,
    *,
    trade_date: date,
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    await session.execute(
        text(
            """
            INSERT INTO factor_values (
                ts_code, trade_date, factor_name, value, normalized_value,
                percentile_rank, data_source, updated_at
            )
            VALUES (
                :ts_code, :trade_date, :factor_name, :value, :normalized_value,
                :percentile_rank, 'computed', NOW()
            )
            ON CONFLICT (ts_code, trade_date, factor_name) DO UPDATE SET
                value = EXCLUDED.value,
                normalized_value = EXCLUDED.normalized_value,
                percentile_rank = EXCLUDED.percentile_rank,
                data_source = EXCLUDED.data_source,
                updated_at = NOW()
            """
        ),
        rows,
    )
    return len(rows)


async def _delete_factor_values_for_date(
    session: AsyncSession,
    *,
    trade_date: date,
    factor_names: list[str],
) -> int:
    if not factor_names:
        return 0
    result = await session.execute(
        text(
            """
            DELETE FROM factor_values
            WHERE trade_date = :trade_date
              AND factor_name = ANY(CAST(:factor_names AS VARCHAR[]))
            """
        ),
        {"trade_date": trade_date, "factor_names": factor_names},
    )
    return getattr(result, "rowcount", 0) or 0


async def _scope_codes(session: AsyncSession, scope_type: str, scope_value: str | None) -> set[str] | None:
    if scope_type == "all":
        return None
    if scope_type != "watchlist_group":
        raise ValueError("scope_type must be 'all' or 'watchlist_group'")
    if not scope_value:
        raise ValueError("scope_value is required for watchlist_group scope")
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ts_code
            FROM watchlist
            WHERE group_name = :group_name
            """
        ),
        {"group_name": scope_value},
    )
    return {row["ts_code"] for row in result.mappings().all()}


async def _delete_scoring_rank_for_scope(
    session: AsyncSession,
    *,
    trade_date: date,
    scope_type: str,
    scope_value: str | None,
) -> int:
    result = await session.execute(
        text(
            """
            DELETE FROM scoring_rank
            WHERE trade_date = :trade_date
              AND scope_type = :scope_type
              AND (
                    (:scope_type = 'all' AND scope_value IS NULL)
                 OR (:scope_type <> 'all' AND scope_value = :scope_value)
              )
            """
        ),
        {"trade_date": trade_date, "scope_type": scope_type, "scope_value": scope_value},
    )
    return getattr(result, "rowcount", 0) or 0


async def _upsert_scoring_rank(
    session: AsyncSession,
    *,
    trade_date: date,
    scope_type: str,
    scope_value: str | None,
    factor_rows: list[dict[str, Any]],
    definitions: list[dict[str, Any]],
) -> int:
    codes = await _scope_codes(session, scope_type, scope_value)
    definition_by_name = {row["name"]: row for row in definitions}
    scores: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    weight_sums: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    breakdown: dict[str, dict[str, Any]] = defaultdict(dict)

    for row in factor_rows:
        ts_code = row["ts_code"]
        if codes is not None and ts_code not in codes:
            continue
        definition = definition_by_name.get(row["factor_name"])
        if definition is None:
            continue
        weight = Decimal(str(definition["default_weight"]))
        normalized = row["normalized_value"]
        if normalized is None:
            continue
        contribution = Decimal(str(normalized)) * weight
        scores[ts_code] += contribution
        weight_sums[ts_code] += weight
        breakdown[ts_code][row["factor_name"]] = {
            "value": row["value"],
            "normalized_value": row["normalized_value"],
            "percentile_rank": row["percentile_rank"],
            "weight": weight,
            "contribution": contribution,
        }

    score_series = pd.Series(
        {code: float(score / weight_sums[code]) for code, score in scores.items() if weight_sums[code] > 0},
        dtype="float64",
    )
    if score_series.empty:
        return 0

    ranked = score_series.sort_values(ascending=False)
    percentile = score_series.rank(pct=True, method="average")
    rows = []
    for rank, (ts_code, score) in enumerate(ranked.items(), start=1):
        rows.append(
            {
                "trade_date": trade_date,
                "ts_code": ts_code,
                "scope_type": scope_type,
                "scope_value": scope_value if scope_type != "all" else None,
                "total_score": Decimal(str(score)).quantize(SCORE_QUANT, rounding=ROUND_HALF_UP),
                "rank": rank,
                "percentile_rank": Decimal(str(float(percentile.loc[ts_code]))).quantize(SCORE_QUANT, rounding=ROUND_HALF_UP),
                "factor_breakdown": _json(_serialize_value(breakdown[ts_code])),
            }
        )

    await session.execute(
        text(
            """
            INSERT INTO scoring_rank (
                trade_date, ts_code, scope_type, scope_value, total_score,
                rank, percentile_rank, factor_breakdown, updated_at
            )
            VALUES (
                :trade_date, :ts_code, :scope_type, :scope_value, :total_score,
                :rank, :percentile_rank, CAST(:factor_breakdown AS JSONB), NOW()
            )
            ON CONFLICT (trade_date, ts_code, scope_type, (COALESCE(scope_value, ''))) DO UPDATE SET
                total_score = EXCLUDED.total_score,
                rank = EXCLUDED.rank,
                percentile_rank = EXCLUDED.percentile_rank,
                factor_breakdown = EXCLUDED.factor_breakdown,
                updated_at = NOW()
            """
        ),
        rows,
    )
    return len(rows)


async def compute_factors_for_date(
    session: AsyncSession,
    *,
    trade_date: date | None = None,
    scope_type: str = "all",
    scope_value: str | None = None,
) -> dict[str, Any]:
    if scope_type == "watchlist_group" and not scope_value:
        raise ValueError("scope_value is required for watchlist_group scope")
    if scope_type not in {"all", "watchlist_group"}:
        raise ValueError("scope_type must be 'all' or 'watchlist_group'")

    run_date = await _last_open_trade_date(session, trade_date)
    if run_date is None:
        return {"skipped": True, "reason": "non-trading day" if trade_date is not None else "no open trade date"}

    await seed_factor_definitions(session, commit=False)
    definitions = await _enabled_factor_definitions(session)
    definition_by_name = {row["name"]: row for row in definitions}
    await _delete_factor_values_for_date(
        session,
        trade_date=run_date,
        factor_names=[name for name in definition_by_name],
    )
    await _delete_scoring_rank_for_scope(
        session,
        trade_date=run_date,
        scope_type=scope_type,
        scope_value=scope_value if scope_type != "all" else None,
    )
    fundamentals = await _latest_fundamentals(session, run_date)
    kline_rows = await _recent_kline_rows(session, run_date)
    raw_values = _compute_raw_factor_values(fundamentals, kline_rows, definitions)

    factor_rows: list[dict[str, Any]] = []
    factor_counts: dict[str, int] = {}
    for factor_name, values in raw_values.items():
        definition = definition_by_name.get(factor_name)
        if definition is None:
            continue
        normalized = normalize_cross_section(values, int(definition["direction"]))
        factor_counts[factor_name] = len(normalized)
        for ts_code, raw_value in values.items():
            normalized_row = normalized.get(ts_code)
            if normalized_row is None:
                continue
            factor_rows.append(
                {
                    "ts_code": ts_code,
                    "trade_date": run_date,
                    "factor_name": factor_name,
                    "value": raw_value,
                    "normalized_value": normalized_row["normalized_value"],
                    "percentile_rank": normalized_row["percentile_rank"],
                }
            )

    values_written = await _upsert_factor_values(session, trade_date=run_date, rows=factor_rows)
    rank_written = await _upsert_scoring_rank(
        session,
        trade_date=run_date,
        scope_type=scope_type,
        scope_value=scope_value,
        factor_rows=factor_rows,
        definitions=definitions,
    )
    await session.commit()
    return {
        "trade_date": run_date.isoformat(),
        "factor_count": len(definitions),
        "factor_value_count": values_written,
        "rank_count": rank_written,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "factor_counts": factor_counts,
    }


async def query_rank(
    session: AsyncSession,
    *,
    trade_date: date | None = None,
    scope_type: str = "all",
    scope_value: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    if scope_type == "watchlist_group" and not scope_value:
        raise ValueError("scope_value is required for watchlist_group scope")
    if scope_type not in {"all", "watchlist_group"}:
        raise ValueError("scope_type must be 'all' or 'watchlist_group'")

    clauses = ["sr.scope_type = :scope_type"]
    params: dict[str, Any] = {"scope_type": scope_type, "limit": page_size, "offset": (page - 1) * page_size}
    if scope_type == "all":
        clauses.append("sr.scope_value IS NULL")
    elif scope_value:
        clauses.append("sr.scope_value = :scope_value")
        params["scope_value"] = scope_value
    if trade_date:
        clauses.append("sr.trade_date = :trade_date")
        params["trade_date"] = trade_date
    else:
        latest_clauses = ["scope_type = :scope_type"]
        if scope_type == "all":
            latest_clauses.append("scope_value IS NULL")
        elif scope_value:
            latest_clauses.append("scope_value = :scope_value")
        clauses.append(
            f"sr.trade_date = (SELECT MAX(trade_date) FROM scoring_rank WHERE {' AND '.join(latest_clauses)})"
        )
    where_sql = " AND ".join(clauses)
    count_result = await session.execute(text(f"SELECT COUNT(*) FROM scoring_rank sr WHERE {where_sql}"), params)
    total = int(count_result.scalar_one())
    result = await session.execute(
        text(
            f"""
            SELECT sr.id, sr.trade_date, sr.ts_code, sb.name AS stock_name,
                   sr.scope_type, sr.scope_value, sr.total_score, sr.rank,
                   sr.percentile_rank, sr.factor_breakdown, sr.created_at, sr.updated_at
            FROM scoring_rank sr
            LEFT JOIN stock_basic sb ON sb.ts_code = sr.ts_code
            WHERE {where_sql}
            ORDER BY sr.rank ASC, sr.total_score DESC, sr.ts_code ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {"items": serialize_rows(list(result.mappings().all())), "page": page, "page_size": page_size, "total": total}


async def query_factor_values(
    session: AsyncSession,
    *,
    trade_date: date,
    factor_name: str,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    params = {"trade_date": trade_date, "factor_name": factor_name, "limit": page_size, "offset": (page - 1) * page_size}
    count_result = await session.execute(
        text("SELECT COUNT(*) FROM factor_values WHERE trade_date = :trade_date AND factor_name = :factor_name"),
        params,
    )
    total = int(count_result.scalar_one())
    result = await session.execute(
        text(
            """
            SELECT fv.ts_code, sb.name AS stock_name, fv.trade_date, fv.factor_name,
                   fv.value, fv.normalized_value, fv.percentile_rank, fv.data_source,
                   fv.created_at, fv.updated_at
            FROM factor_values fv
            LEFT JOIN stock_basic sb ON sb.ts_code = fv.ts_code
            WHERE fv.trade_date = :trade_date AND fv.factor_name = :factor_name
            ORDER BY fv.percentile_rank DESC NULLS LAST, fv.ts_code ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {"items": serialize_rows(list(result.mappings().all())), "page": page, "page_size": page_size, "total": total}


async def query_factor_analysis(
    session: AsyncSession,
    *,
    factor_name: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    clauses = ["TRUE"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if factor_name:
        clauses.append("fa.factor_name = :factor_name")
        params["factor_name"] = factor_name
    where_sql = " AND ".join(clauses)
    count_result = await session.execute(text(f"SELECT COUNT(*) FROM factor_analysis fa WHERE {where_sql}"), params)
    total = int(count_result.scalar_one())
    result = await session.execute(
        text(
            f"""
            SELECT fa.id, fa.factor_name, fd.display_name, fa.period_start, fa.period_end,
                   fa.forward_days, fa.ic, fa.ic_mean, fa.ic_std, fa.ir, fa.icir,
                   fa.ic_gt_0_pct, fa.group_returns, fa.details, fa.created_at, fa.updated_at
            FROM factor_analysis fa
            LEFT JOIN factor_definitions fd ON fd.name = fa.factor_name
            WHERE {where_sql}
            ORDER BY fa.period_end DESC, fa.factor_name ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {"items": serialize_rows(list(result.mappings().all())), "page": page, "page_size": page_size, "total": total}


async def analyze_factor_icir(
    session: AsyncSession,
    *,
    factor_name: str,
    period_start: date,
    period_end: date,
    forward_days: int = 5,
) -> dict[str, Any]:
    if forward_days <= 0:
        raise ValueError("forward_days must be positive")
    result = await session.execute(
        text(
            """
            SELECT fv.ts_code, fv.trade_date,
                   COALESCE(fv.normalized_value, fv.value) AS factor_value,
                   current_k.close AS current_close,
                   future_k.close AS future_close,
                   (future_k.close / NULLIF(current_k.close, 0) - 1) AS forward_return
            FROM factor_values fv
            JOIN daily_kline current_k
              ON current_k.ts_code = fv.ts_code
             AND current_k.trade_date = fv.trade_date
            JOIN LATERAL (
                SELECT dk.close
                FROM daily_kline dk
                WHERE dk.ts_code = fv.ts_code
                  AND dk.trade_date > fv.trade_date
                  AND dk.close IS NOT NULL
                  AND dk.is_suspended = FALSE
                ORDER BY dk.trade_date ASC
                OFFSET :offset_days
                LIMIT 1
            ) future_k ON TRUE
            WHERE fv.factor_name = :factor_name
              AND fv.trade_date BETWEEN :period_start AND :period_end
              AND fv.value IS NOT NULL
              AND current_k.close IS NOT NULL
            ORDER BY fv.trade_date, fv.ts_code
            """
        ),
        {
            "factor_name": factor_name,
            "period_start": period_start,
            "period_end": period_end,
            "offset_days": forward_days - 1,
        },
    )
    rows = [dict(row) for row in result.mappings().all()]
    if not rows:
        raise ValueError("no factor values with forward returns for analysis period")

    frame = pd.DataFrame(rows)
    frame["factor_value"] = pd.to_numeric(frame["factor_value"], errors="coerce")
    frame["forward_return"] = pd.to_numeric(frame["forward_return"], errors="coerce")
    frame = frame.dropna(subset=["factor_value", "forward_return"])

    ic_by_date: list[dict[str, Any]] = []
    for trade_day, group in frame.groupby("trade_date"):
        if len(group) < 2 or group["factor_value"].nunique() < 2 or group["forward_return"].nunique() < 2:
            continue
        corr = group["factor_value"].corr(group["forward_return"])
        if corr is not None and isfinite(float(corr)):
            ic_by_date.append({"trade_date": _date(trade_day).isoformat(), "ic": float(corr), "count": int(len(group))})
    if not ic_by_date:
        raise ValueError("not enough cross-sectional variation to calculate IC")

    ic_series = pd.Series([item["ic"] for item in ic_by_date], dtype="float64")
    ic_mean = float(ic_series.mean())
    ic_std = float(ic_series.std(ddof=0))
    ir = ic_mean / ic_std if ic_std else None
    latest_ic = float(ic_series.iloc[-1])
    ic_gt_0_pct = float((ic_series > 0).mean())

    group_returns: dict[str, float] = {}
    try:
        frame["bucket"] = frame.groupby("trade_date")["factor_value"].transform(
            lambda series: pd.qcut(series.rank(method="first"), 5, labels=False, duplicates="drop") + 1
        )
        group_returns = {
            str(int(bucket)): float(value)
            for bucket, value in frame.groupby("bucket")["forward_return"].mean().dropna().items()
        }
    except ValueError:
        group_returns = {}

    payload = {
        "factor_name": factor_name,
        "period_start": period_start,
        "period_end": period_end,
        "forward_days": forward_days,
        "ic": _to_decimal(latest_ic),
        "ic_mean": _to_decimal(ic_mean),
        "ic_std": _to_decimal(ic_std),
        "ir": _to_decimal(ir) if ir is not None else None,
        "icir": _to_decimal(ir) if ir is not None else None,
        "ic_gt_0_pct": _to_decimal(ic_gt_0_pct),
        "group_returns": _json(group_returns),
        "details": _json({"ic_by_date": ic_by_date, "sample_count": int(len(frame))}),
    }
    await session.execute(
        text(
            """
            INSERT INTO factor_analysis (
                factor_name, period_start, period_end, forward_days, ic, ic_mean,
                ic_std, ir, icir, ic_gt_0_pct, group_returns, details, updated_at
            )
            VALUES (
                :factor_name, :period_start, :period_end, :forward_days, :ic, :ic_mean,
                :ic_std, :ir, :icir, :ic_gt_0_pct, CAST(:group_returns AS JSONB),
                CAST(:details AS JSONB), NOW()
            )
            ON CONFLICT (factor_name, period_start, period_end, forward_days) DO UPDATE SET
                ic = EXCLUDED.ic,
                ic_mean = EXCLUDED.ic_mean,
                ic_std = EXCLUDED.ic_std,
                ir = EXCLUDED.ir,
                icir = EXCLUDED.icir,
                ic_gt_0_pct = EXCLUDED.ic_gt_0_pct,
                group_returns = EXCLUDED.group_returns,
                details = EXCLUDED.details,
                updated_at = NOW()
            """
        ),
        payload,
    )
    await session.commit()
    return {
        "factor_name": factor_name,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "forward_days": forward_days,
        "ic": str(payload["ic"]),
        "ic_mean": str(payload["ic_mean"]),
        "ic_std": str(payload["ic_std"]),
        "ir": str(payload["ir"]) if payload["ir"] is not None else None,
        "icir": str(payload["icir"]) if payload["icir"] is not None else None,
        "ic_gt_0_pct": str(payload["ic_gt_0_pct"]),
        "sample_count": int(len(frame)),
        "ic_count": len(ic_by_date),
    }
