from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fetcher import DataProvider, default_providers, fetch_with_fallback, get_data_proxy_url
from app.data.normalizers import normalize_ts_code
from app.data.repository import (
    create_alert,
    record_update_failure,
    record_update_success,
    upsert_stock_fundamentals,
)

LOCAL_USER_ID = 1

PINYIN_INITIAL_RANGES = [
    (-20319, -20284, "a"),
    (-20283, -19776, "b"),
    (-19775, -19219, "c"),
    (-19218, -18711, "d"),
    (-18710, -18527, "e"),
    (-18526, -18240, "f"),
    (-18239, -17923, "g"),
    (-17922, -17418, "h"),
    (-17417, -16475, "j"),
    (-16474, -16213, "k"),
    (-16212, -15641, "l"),
    (-15640, -15166, "m"),
    (-15165, -14923, "n"),
    (-14922, -14915, "o"),
    (-14914, -14631, "p"),
    (-14630, -14150, "q"),
    (-14149, -14091, "r"),
    (-14090, -13319, "s"),
    (-13318, -12839, "t"),
    (-12838, -12557, "w"),
    (-12556, -11848, "x"),
    (-11847, -11056, "y"),
    (-11055, -10247, "z"),
]

PINYIN_INITIAL_OVERRIDES = {
    "行": "h",
    "重": "c",
    "长": "c",
    "厦": "x",
}


@dataclass(slots=True)
class StockFilters:
    query: str | None = None
    market: str | list[str] | None = None
    exchange: str | None = None
    industry: str | None = None
    exclude_st: bool = False
    exclude_delisted: bool = True
    pe_min: Decimal | None = None
    pe_max: Decimal | None = None
    pb_min: Decimal | None = None
    pb_max: Decimal | None = None
    market_cap_min: Decimal | None = None
    market_cap_max: Decimal | None = None


def _pinyin_initial(char: str) -> str:
    if char in PINYIN_INITIAL_OVERRIDES:
        return PINYIN_INITIAL_OVERRIDES[char]
    if char.isascii():
        return char.lower() if char.isalnum() else ""
    try:
        encoded = char.encode("gb2312")
    except UnicodeEncodeError:
        return ""
    if len(encoded) < 2:
        return ""
    code = encoded[0] * 256 + encoded[1] - 65536
    for start, end, initial in PINYIN_INITIAL_RANGES:
        if start <= code <= end:
            return initial
    return ""


def stock_name_initials(name: str | None) -> str:
    return "".join(_pinyin_initial(char) for char in name or "")


def _matches_stock_query(row: dict[str, Any], query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    code_needle = needle.upper()
    ts_code = str(row.get("ts_code") or "")
    symbol = str(row.get("symbol") or "")
    name = str(row.get("name") or "")
    return (
        code_needle in ts_code.upper()
        or code_needle in symbol.upper()
        or needle in name.lower()
        or needle in stock_name_initials(name)
    )

def _range_filter(
    field: str,
    min_value: Decimal | None,
    max_value: Decimal | None,
    clauses: list[str],
    params: dict[str, Any],
    prefix: str,
) -> None:
    if min_value is None and max_value is None:
        return
    clauses.append(f"{field} IS NOT NULL")
    if min_value is not None:
        clauses.append(f"{field} >= :{prefix}_min")
        params[f"{prefix}_min"] = min_value
    if max_value is not None:
        clauses.append(f"{field} <= :{prefix}_max")
        params[f"{prefix}_max"] = max_value


def _stock_where(filters: StockFilters, *, include_query: bool = True) -> tuple[list[str], dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if include_query and filters.query:
        clauses.append("(s.ts_code ILIKE :query OR s.symbol ILIKE :query OR s.name ILIKE :query)")
        params["query"] = f"%{filters.query.strip()}%"
    if filters.market:
        markets = filters.market if isinstance(filters.market, list) else [filters.market]
        markets = [market.strip() for market in markets if market and market.strip()]
        if markets:
            names = []
            for index, market in enumerate(markets):
                key = f"market_{index}"
                names.append(f":{key}")
                params[key] = market
            clauses.append(f"s.market IN ({', '.join(names)})")
    if filters.exchange:
        clauses.append("s.exchange = :exchange")
        params["exchange"] = filters.exchange.strip().upper()
    if filters.industry:
        clauses.append("s.industry = :industry")
        params["industry"] = filters.industry.strip()
    if filters.exclude_st:
        clauses.append("s.is_st = FALSE")
    if filters.exclude_delisted:
        clauses.append("(s.is_delisted = FALSE AND s.delist_date IS NULL)")
    _range_filter("f.pe_ttm", filters.pe_min, filters.pe_max, clauses, params, "pe")
    _range_filter("f.pb", filters.pb_min, filters.pb_max, clauses, params, "pb")
    _range_filter("f.market_cap", filters.market_cap_min, filters.market_cap_max, clauses, params, "market_cap")
    return clauses, params


def _filters_from_dict(filters: dict[str, Any]) -> StockFilters:
    def dec(key: str) -> Decimal | None:
        value = filters.get(key)
        return Decimal(str(value)) if value not in (None, "") else None

    def range_dec(key: str, bound: str) -> Decimal | None:
        value = filters.get(key)
        if not isinstance(value, dict):
            return None
        raw = value.get(bound)
        return Decimal(str(raw)) if raw not in (None, "") else None

    exchange = filters.get("exchange")
    market = filters.get("market")
    industry = filters.get("industry")
    if isinstance(exchange, list):
        exchange = exchange[0] if exchange else None
    if isinstance(industry, list):
        industry = industry[0] if industry else None

    return StockFilters(
        query=filters.get("query"),
        market=market,
        exchange=exchange,
        industry=industry,
        exclude_st=bool(filters.get("exclude_st", False)),
        exclude_delisted=bool(filters.get("exclude_delisted", True)),
        pe_min=dec("pe_min") or range_dec("pe_ttm", "min"),
        pe_max=dec("pe_max") or range_dec("pe_ttm", "max"),
        pb_min=dec("pb_min") or range_dec("pb", "min"),
        pb_max=dec("pb_max") or range_dec("pb", "max"),
        market_cap_min=dec("market_cap_min") or range_dec("market_cap", "min"),
        market_cap_max=dec("market_cap_max") or range_dec("market_cap", "max"),
    )


async def list_stocks(session: AsyncSession, filters: StockFilters, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    if filters.query and filters.query.strip():
        return await _list_stocks_with_fuzzy_query(session, filters, page=page, page_size=page_size)

    clauses, params = _stock_where(filters)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.update({"limit": page_size, "offset": (page - 1) * page_size})

    count_result = await session.execute(
        text(
            f"""
            WITH latest_fundamentals AS (
                SELECT DISTINCT ON (ts_code) *
                FROM stock_fundamentals
                ORDER BY ts_code, report_date DESC
            )
            SELECT COUNT(*)
            FROM stock_basic s
            LEFT JOIN latest_fundamentals f ON f.ts_code = s.ts_code
            {where_sql}
            """
        ),
        params,
    )
    total = int(count_result.scalar_one())

    rows_result = await session.execute(
        text(
            f"""
            WITH latest_fundamentals AS (
                SELECT DISTINCT ON (ts_code) *
                FROM stock_fundamentals
                ORDER BY ts_code, report_date DESC
            ),
            latest_kline AS (
                SELECT DISTINCT ON (ts_code) ts_code, trade_date, close
                FROM daily_kline
                ORDER BY ts_code, trade_date DESC
            ),
            kline_counts AS (
                SELECT ts_code, COUNT(*) AS count
                FROM daily_kline
                GROUP BY ts_code
            )
            SELECT
                s.ts_code, s.symbol, s.name, s.market, s.exchange, s.industry, s.area,
                s.list_date, s.delist_date, s.is_st, s.is_delisted,
                f.report_date, f.pe_ttm, f.pb, f.ps_ttm, f.pcf_ttm,
                f.market_cap, f.float_market_cap, f.data_source AS fundamentals_source,
                k.trade_date AS latest_trade_date, k.close AS latest_close,
                COALESCE(kc.count, 0) AS daily_kline_count
            FROM stock_basic s
            LEFT JOIN latest_fundamentals f ON f.ts_code = s.ts_code
            LEFT JOIN latest_kline k ON k.ts_code = s.ts_code
            LEFT JOIN kline_counts kc ON kc.ts_code = s.ts_code
            {where_sql}
            ORDER BY s.symbol
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {
        "items": [dict(row) for row in rows_result.mappings().all()],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


async def _list_stocks_with_fuzzy_query(
    session: AsyncSession,
    filters: StockFilters,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    clauses, params = _stock_where(filters, include_query=False)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    result = await session.execute(
        text(
            f"""
            WITH latest_fundamentals AS (
                SELECT DISTINCT ON (ts_code) *
                FROM stock_fundamentals
                ORDER BY ts_code, report_date DESC
            ),
            latest_kline AS (
                SELECT DISTINCT ON (ts_code) ts_code, trade_date, close
                FROM daily_kline
                ORDER BY ts_code, trade_date DESC
            ),
            kline_counts AS (
                SELECT ts_code, COUNT(*) AS count
                FROM daily_kline
                GROUP BY ts_code
            )
            SELECT
                s.ts_code, s.symbol, s.name, s.market, s.exchange, s.industry, s.area,
                s.list_date, s.delist_date, s.is_st, s.is_delisted,
                f.report_date, f.pe_ttm, f.pb, f.ps_ttm, f.pcf_ttm,
                f.market_cap, f.float_market_cap, f.data_source AS fundamentals_source,
                k.trade_date AS latest_trade_date, k.close AS latest_close,
                COALESCE(kc.count, 0) AS daily_kline_count
            FROM stock_basic s
            LEFT JOIN latest_fundamentals f ON f.ts_code = s.ts_code
            LEFT JOIN latest_kline k ON k.ts_code = s.ts_code
            LEFT JOIN kline_counts kc ON kc.ts_code = s.ts_code
            {where_sql}
            ORDER BY s.symbol
            """
        ),
        params,
    )
    rows = [dict(row) for row in result.mappings().all()]
    matched = [row for row in rows if _matches_stock_query(row, filters.query or "")]
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": matched[start:end],
        "page": page,
        "page_size": page_size,
        "total": len(matched),
    }


async def get_klines(
    session: AsyncSession,
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    code = normalize_ts_code(ts_code)
    clauses = ["ts_code = :ts_code"]
    params: dict[str, Any] = {"ts_code": code}
    if start_date:
        clauses.append("trade_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("trade_date <= :end_date")
        params["end_date"] = end_date
    result = await session.execute(
        text(
            f"""
            SELECT ts_code, trade_date, open, high, low, close, pre_close, volume,
                   amount, turnover_rate, adj_factor, is_suspended, is_limit_up,
                   is_limit_down, data_source
            FROM daily_kline
            WHERE {" AND ".join(clauses)}
            ORDER BY trade_date
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


async def stock_exists(session: AsyncSession, ts_code: str) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM stock_basic WHERE ts_code = :ts_code"),
        {"ts_code": normalize_ts_code(ts_code)},
    )
    return result.scalar_one_or_none() is not None


async def list_watchlist(
    session: AsyncSession,
    user_id: int = LOCAL_USER_ID,
    group_name: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["w.user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id}
    if group_name:
        clauses.append("w.group_name = :group_name")
        params["group_name"] = group_name
    result = await session.execute(
        text(
            f"""
            WITH latest_kline AS (
                SELECT DISTINCT ON (ts_code) ts_code, trade_date, close, pre_close
                FROM daily_kline
                ORDER BY ts_code, trade_date DESC
            )
            SELECT
                w.id, w.group_name, w.ts_code, w.sort_order, w.note, w.added_at,
                s.name, s.industry, s.market, s.exchange, s.is_st, s.is_delisted,
                k.trade_date AS latest_trade_date, k.close AS latest_close,
                k.pre_close AS pre_close
            FROM watchlist w
            JOIN stock_basic s ON s.ts_code = w.ts_code
            LEFT JOIN latest_kline k ON k.ts_code = w.ts_code
            WHERE {" AND ".join(clauses)}
            ORDER BY w.group_name, w.sort_order, w.added_at DESC
            """
        ),
        params,
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        item = dict(row)
        groups.setdefault(item["group_name"], []).append(item)
    return [{"group_name": name, "items": items} for name, items in groups.items()]


async def list_watchlist_groups(session: AsyncSession, user_id: int = LOCAL_USER_ID) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT g.group_name, COUNT(w.id) AS item_count
            FROM watchlist_groups g
            LEFT JOIN watchlist w
              ON w.user_id = g.user_id
             AND w.group_name = g.group_name
            WHERE g.user_id = :user_id
            GROUP BY g.group_name
            ORDER BY g.group_name
            """
        ),
        {"user_id": user_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def create_watchlist_group(
    session: AsyncSession,
    group_name: str,
    user_id: int = LOCAL_USER_ID,
) -> dict[str, Any]:
    name = group_name.strip() or "默认"
    result = await session.execute(
        text(
            """
            INSERT INTO watchlist_groups (user_id, group_name, updated_at)
            VALUES (:user_id, :group_name, NOW())
            ON CONFLICT (user_id, group_name) DO NOTHING
            RETURNING id, group_name, created_at, updated_at
            """
        ),
        {"user_id": user_id, "group_name": name},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ValueError(f"watchlist group already exists: {name}")
    await session.commit()
    return dict(row)


async def rename_watchlist_group(
    session: AsyncSession,
    old_group_name: str,
    new_group_name: str,
    user_id: int = LOCAL_USER_ID,
) -> dict[str, Any] | None:
    old_name = old_group_name.strip()
    new_name = new_group_name.strip() or "默认"
    if not old_name:
        return None
    exists = await session.execute(
        text(
            """
            SELECT 1
            FROM watchlist_groups
            WHERE user_id = :user_id AND group_name = :new_group_name
            """
        ),
        {"user_id": user_id, "new_group_name": new_name},
    )
    if old_name != new_name and exists.scalar_one_or_none() is not None:
        raise ValueError(f"watchlist group already exists: {new_name}")

    result = await session.execute(
        text(
            """
            UPDATE watchlist_groups
            SET group_name = :new_group_name,
                updated_at = NOW()
            WHERE user_id = :user_id AND group_name = :old_group_name
            RETURNING id, group_name, created_at, updated_at
            """
        ),
        {"user_id": user_id, "old_group_name": old_name, "new_group_name": new_name},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None

    await session.execute(
        text(
            """
            UPDATE watchlist
            SET group_name = :new_group_name,
                updated_at = NOW()
            WHERE user_id = :user_id AND group_name = :old_group_name
            """
        ),
        {"user_id": user_id, "old_group_name": old_name, "new_group_name": new_name},
    )
    await session.commit()
    return dict(row)


async def delete_watchlist_group(
    session: AsyncSession,
    group_name: str,
    user_id: int = LOCAL_USER_ID,
) -> bool:
    name = group_name.strip()
    if not name:
        return False
    if name == "默认":
        raise ValueError("default watchlist group cannot be deleted")

    await session.execute(
        text(
            """
            INSERT INTO watchlist_groups (user_id, group_name, updated_at)
            VALUES (:user_id, '默认', NOW())
            ON CONFLICT (user_id, group_name) DO NOTHING
            """
        ),
        {"user_id": user_id},
    )
    await session.execute(
        text(
            """
            DELETE FROM watchlist source
            USING watchlist target
            WHERE source.user_id = :user_id
              AND source.group_name = :group_name
              AND target.user_id = :user_id
              AND target.group_name = '默认'
              AND target.ts_code = source.ts_code
            """
        ),
        {"user_id": user_id, "group_name": name},
    )
    await session.execute(
        text(
            """
            UPDATE watchlist
            SET group_name = '默认',
                updated_at = NOW()
            WHERE user_id = :user_id AND group_name = :group_name
            """
        ),
        {"user_id": user_id, "group_name": name},
    )
    result = await session.execute(
        text(
            """
            DELETE FROM watchlist_groups
            WHERE user_id = :user_id AND group_name = :group_name
            """
        ),
        {"user_id": user_id, "group_name": name},
    )
    await session.commit()
    return result.rowcount > 0


async def add_watchlist_item(
    session: AsyncSession,
    *,
    ts_code: str,
    group_name: str = "默认",
    note: str | None = None,
    sort_order: int = 0,
    user_id: int = LOCAL_USER_ID,
) -> dict[str, Any]:
    code = normalize_ts_code(ts_code)
    if not await stock_exists(session, code):
        raise ValueError(f"unknown ts_code: {code}")
    await session.execute(
        text(
            """
            INSERT INTO watchlist_groups (user_id, group_name, updated_at)
            VALUES (:user_id, :group_name, NOW())
            ON CONFLICT (user_id, group_name) DO NOTHING
            """
        ),
        {"user_id": user_id, "group_name": group_name or "默认"},
    )
    result = await session.execute(
        text(
            """
            INSERT INTO watchlist (user_id, group_name, ts_code, note, sort_order, updated_at)
            VALUES (:user_id, :group_name, :ts_code, :note, :sort_order, NOW())
            ON CONFLICT (user_id, group_name, ts_code) DO UPDATE SET
                note = EXCLUDED.note,
                sort_order = EXCLUDED.sort_order,
                updated_at = NOW()
            RETURNING id, group_name, ts_code, note, sort_order, added_at
            """
        ),
        {
            "user_id": user_id,
            "group_name": group_name or "默认",
            "ts_code": code,
            "note": note,
            "sort_order": sort_order,
        },
    )
    await session.commit()
    return dict(result.mappings().one())


async def update_watchlist_item(
    session: AsyncSession,
    item_id: int,
    *,
    group_name: str | None = None,
    note: str | None = None,
    sort_order: int | None = None,
    user_id: int = LOCAL_USER_ID,
) -> dict[str, Any] | None:
    updates = []
    params: dict[str, Any] = {"item_id": item_id, "user_id": user_id}
    if group_name is not None:
        updates.append("group_name = :group_name")
        params["group_name"] = group_name
    if note is not None:
        updates.append("note = :note")
        params["note"] = note
    if sort_order is not None:
        updates.append("sort_order = :sort_order")
        params["sort_order"] = sort_order
    if not updates:
        current = await session.execute(
            text(
                """
                SELECT id, group_name, ts_code, note, sort_order, added_at
                FROM watchlist
                WHERE id = :item_id AND user_id = :user_id
                """
            ),
            params,
        )
        row = current.mappings().one_or_none()
        return dict(row) if row else None

    result = await session.execute(
        text(
            f"""
            UPDATE watchlist
            SET {", ".join(updates)},
                updated_at = NOW()
            WHERE id = :item_id AND user_id = :user_id
            RETURNING id, group_name, ts_code, note, sort_order, added_at
            """
        ),
        params,
    )
    row = result.mappings().one_or_none()
    await session.commit()
    return dict(row) if row else None


async def delete_watchlist_item(session: AsyncSession, item_id: int, user_id: int = LOCAL_USER_ID) -> bool:
    result = await session.execute(
        text("DELETE FROM watchlist WHERE id = :item_id AND user_id = :user_id"),
        {"item_id": item_id, "user_id": user_id},
    )
    await session.commit()
    return result.rowcount > 0


async def sync_fundamentals(
    session: AsyncSession,
    ts_codes: list[str] | None,
    start_date: date,
    end_date: date,
    providers: list[DataProvider] | None = None,
) -> dict[str, Any]:
    provider_list = providers or default_providers()
    codes = [normalize_ts_code(code) for code in ts_codes] if ts_codes else await _select_fundamental_codes(session)
    if not codes:
        raise ValueError("no stock codes available for fundamentals sync")

    total = 0
    failures: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}

    for code in codes:
        try:
            source, records = fetch_with_fallback(provider_list, "fetch_stock_fundamentals", [code], start_date, end_date, proxy_url=get_data_proxy_url())
            count = await upsert_stock_fundamentals(session, records)
            total += count
            source_counts[source] = source_counts.get(source, 0) + count
            latest = max((record.report_date for record in records), default=None)
            await record_update_success(session, "fundamentals", source, ts_code=code, last_trade_date=latest)
        except Exception as exc:
            message = str(exc)
            failures.append({"ts_code": code, "error": message})
            await record_update_failure(session, "fundamentals", "fallback", message, ts_code=code)

    if failures:
        await create_alert(
            session,
            level="warning" if total else "error",
            category="data_sync",
            title="Fundamentals sync completed with failures",
            message=f"{len(failures)} symbols failed during fundamentals sync",
            payload={"failures": failures[:20]},
        )
    await session.commit()
    if total == 0 and failures:
        raise RuntimeError(f"all fundamentals sync attempts failed: {failures[0]['error']}")
    return {
        "requested_symbols": len(codes),
        "inserted_or_updated": total,
        "source_counts": source_counts,
        "failures": failures,
        "start_date": start_date,
        "end_date": end_date,
    }


async def _select_fundamental_codes(session: AsyncSession, limit: int = 100) -> list[str]:
    result = await session.execute(
        text(
            """
            SELECT ts_code
            FROM stock_basic
            WHERE is_delisted = FALSE
            ORDER BY symbol
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [row[0] for row in result.all()]
