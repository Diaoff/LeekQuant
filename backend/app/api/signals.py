"""Signal log query API."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.sim.service import serialize_rows

router = APIRouter(prefix="/api/signals", tags=["signals"])


def _extract_user_id(request: Request) -> int:
    raw = request.headers.get("X-User-ID") or request.query_params.get("user_id")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 1
    return 1


@router.get("")
async def list_signals(
    req: Request,
    strategy_id: int | None = None,
    account_id: int | None = None,
    ts_code: str | None = None,
    signal_type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user_id = _extract_user_id(req)
    clauses = ["sl.user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id}
    if strategy_id is not None:
        clauses.append("sl.strategy_id = :strategy_id")
        params["strategy_id"] = strategy_id
    if account_id is not None:
        clauses.append("sl.account_id = :account_id")
        params["account_id"] = account_id
    if ts_code:
        clauses.append("sl.ts_code = :ts_code")
        params["ts_code"] = ts_code.strip().upper()
    if signal_type:
        clauses.append("sl.signal_type = :signal_type")
        params["signal_type"] = signal_type
    if start_date:
        clauses.append("sl.trade_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("sl.trade_date <= :end_date")
        params["end_date"] = end_date

    where_sql = " AND ".join(clauses)
    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM signal_log sl WHERE {where_sql}"),
        params,
    )
    total = int(count_result.scalar_one())
    summary_result = await session.execute(
        text(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE sl.signal_type = '买入') AS buy_count,
                COUNT(*) FILTER (WHERE sl.signal_type = '增持') AS add_count,
                COUNT(*) FILTER (WHERE sl.signal_type = '减仓') AS reduce_count,
                COUNT(*) FILTER (WHERE sl.signal_type = '卖出') AS sell_count,
                COUNT(*) FILTER (WHERE sl.signal_type = '观望') AS hold_count,
                COUNT(*) FILTER (WHERE sl.action = 'BLOCKED') AS blocked_count
            FROM signal_log sl
            WHERE {where_sql}
            """
        ),
        params,
    )
    summary_row = summary_result.mappings().one()
    params.update({"limit": page_size, "offset": (page - 1) * page_size})
    result = await session.execute(
        text(
            f"""
            SELECT sl.id, sl.user_id, sl.strategy_id, s.name AS strategy_name,
                   sl.account_id, a.name AS account_name, sl.ts_code, sb.name AS stock_name,
                   sl.trade_date, sl.signal_type, sl.target_position,
                   sl.current_position, sl.action, sl.confidence, sl.reason,
                   sl.snapshot, sl.created_at
            FROM signal_log sl
            LEFT JOIN strategies s ON s.id = sl.strategy_id
            LEFT JOIN sim_accounts a ON a.id = sl.account_id
            LEFT JOIN stock_basic sb ON sb.ts_code = sl.ts_code
            WHERE {where_sql}
            ORDER BY sl.trade_date DESC, sl.created_at DESC, sl.id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {
        "items": serialize_rows(list(result.mappings().all())),
        "page": page,
        "page_size": page_size,
        "total": total,
        "summary": {
            "buy_count": int(summary_row["buy_count"] or 0),
            "add_count": int(summary_row["add_count"] or 0),
            "reduce_count": int(summary_row["reduce_count"] or 0),
            "sell_count": int(summary_row["sell_count"] or 0),
            "hold_count": int(summary_row["hold_count"] or 0),
            "blocked_count": int(summary_row["blocked_count"] or 0),
        },
    }
