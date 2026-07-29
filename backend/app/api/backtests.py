"""Backtest submission and status API endpoints."""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from celery import chain

from app.backtest.tasks import run_backtest_task
from app.data.stock_service import get_klines
from app.db.session import get_session
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

MARKET_TARGETS = {"主板", "创业板", "科创板", "北交所"}
MARKET_TARGET_ORDER = ("主板", "创业板", "科创板", "北交所")


def _extract_user_id(request: Request) -> int:
    user_id: int | None = None

    user_id_header = request.headers.get("X-User-ID")
    if user_id_header:
        try:
            user_id = int(user_id_header)
        except (ValueError, TypeError):
            pass

    if user_id is None:
        user_id_query = request.query_params.get("user_id")
        if user_id_query:
            try:
                user_id = int(user_id_query)
            except (ValueError, TypeError):
                pass

    return user_id or 1


def _serialize_kline_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": str(row["trade_date"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        }
        for row in rows
    ]


class BacktestCreateRequest(BaseModel):
    strategy_id: int
    start_date: date
    end_date: date
    initial_cash: float = 100000
    benchmark_code: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    target_type: Literal["all", "market", "watchlist_group"] = "all"
    target_value: str | list[str] | None = None
    exclude_st: bool | None = None
    exclude_loss_pe: bool | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "BacktestCreateRequest":
        if self.target_type == "all":
            self.target_value = None
        elif self.target_type == "market":
            markets = _normalize_market_targets(self.target_value)
            if not markets:
                raise ValueError("target_value must be one of 主板 / 创业板 / 科创板 / 北交所")
            self.target_value = markets
        else:
            if not isinstance(self.target_value, str) or not self.target_value.strip():
                raise ValueError("target_value is required for watchlist_group")
            self.target_value = self.target_value.strip()

        default_filter = self.target_type in {"all", "market"}
        if self.exclude_st is None:
            self.exclude_st = default_filter
        if self.exclude_loss_pe is None:
            self.exclude_loss_pe = default_filter
        return self


def _decode_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _normalize_market_targets(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    selected = [str(item).strip() for item in raw_values if item is not None and str(item).strip()]
    if not selected or any(market not in MARKET_TARGETS for market in selected):
        return []
    selected_set = set(selected)
    return [market for market in MARKET_TARGET_ORDER if market in selected_set]


def _target_label(target_type: str | None, target_value: Any) -> str:
    if target_type == "market" and target_value:
        markets = _normalize_market_targets(target_value)
        return "、".join(markets) if markets else "全市场"
    if target_type == "watchlist_group" and target_value:
        return f"自选股/{str(target_value)}"
    return "全市场"


async def _create_backtest_record(
    session: AsyncSession,
    user_id: int,
    strategy_id: int,
    start_date: date,
    end_date: date,
    initial_cash: float,
    benchmark_code: str | None,
    params_snapshot: dict,
) -> int:
    result = await session.execute(
        text(
            """
            INSERT INTO backtest_results (
                user_id, strategy_id, start_date, end_date,
                initial_cash, benchmark_code, params_snapshot, task_id
            ) VALUES (
                :user_id, :strategy_id, :start_date, :end_date,
                :initial_cash, :benchmark_code, CAST(:params AS JSONB), ''
            )
            RETURNING id
            """
        ),
        {
            "user_id": user_id,
            "strategy_id": strategy_id,
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
            "benchmark_code": benchmark_code or "",
            "params": json.dumps(params_snapshot, ensure_ascii=False, default=str),
        },
    )
    return dict(result.mappings().one())["id"]


class BatchBacktestRequest(BaseModel):
    strategy_ids: list[int] = Field(min_length=1, max_length=20)
    start_date: date
    end_date: date
    initial_cash: float = 100000
    benchmark_code: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    target_type: Literal["all", "market", "watchlist_group"] = "all"
    target_value: str | list[str] | None = None
    exclude_st: bool | None = None
    exclude_loss_pe: bool | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "BatchBacktestRequest":
        if self.target_type == "all":
            self.target_value = None
        elif self.target_type == "market":
            markets = _normalize_market_targets(self.target_value)
            if not markets:
                raise ValueError("target_value must be one of 主板 / 创业板 / 科创板 / 北交所")
            self.target_value = markets
        else:
            if not isinstance(self.target_value, str) or not self.target_value.strip():
                raise ValueError("target_value is required for watchlist_group")
            self.target_value = self.target_value.strip()

        default_filter = self.target_type in {"all", "market"}
        if self.exclude_st is None:
            self.exclude_st = default_filter
        if self.exclude_loss_pe is None:
            self.exclude_loss_pe = default_filter
        return self


def _with_target_fields(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = _decode_json_dict(row.get("params_snapshot"))
    target = _decode_json_dict(snapshot.get("target"))
    target_type = target.get("type") or snapshot.get("target_type") or "all"
    target_value = target.get("value") or snapshot.get("target_value")
    row["target_type"] = target_type
    row["target_value"] = target_value
    row["target_label"] = _target_label(target_type, target_value)
    return row


@router.post("/{strategy_id}/run", status_code=status.HTTP_201_CREATED)
async def run_backtest_now(
    strategy_id: int,
    req: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    initial_cash: float = 100000,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    today = date.today()
    s_date = start_date or date(today.year - 1, today.month, today.day)
    e_date = end_date or today

    return await submit_backtest(
        req,
        BacktestCreateRequest(
            strategy_id=strategy_id,
            start_date=s_date,
            end_date=e_date,
            initial_cash=initial_cash,
            benchmark_code=None,
            config={},
            target_type="all",
            target_value=None,
        ),
        session,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit_backtest(
    req: Request,
    request: BacktestCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user_id = _extract_user_id(req)
    strategy = await session.execute(
        text("SELECT id FROM strategies WHERE id = :id AND user_id = :user_id"),
        {"id": request.strategy_id, "user_id": user_id},
    )
    row = strategy.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")

    params_snapshot = {
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "initial_cash": request.initial_cash,
        "benchmark_code": request.benchmark_code,
        "config": request.config,
        "filters": {
            "exclude_st": bool(request.exclude_st),
            "exclude_loss_pe": bool(request.exclude_loss_pe),
        },
        "target": {
            "type": request.target_type,
            "value": request.target_value,
            "label": _target_label(request.target_type, request.target_value),
        },
    }

    backtest_id = await _create_backtest_record(
        session, user_id, request.strategy_id,
        request.start_date, request.end_date,
        request.initial_cash, request.benchmark_code,
        params_snapshot,
    )

    task_id = uuid4().hex
    await session.execute(
        text("UPDATE backtest_results SET task_id = :task_id, status = 'pending' WHERE id = :id"),
        {"task_id": task_id, "id": backtest_id},
    )
    await session.commit()

    try:
        run_backtest_task.apply_async(
            kwargs={"backtest_id": backtest_id},
            task_id=task_id,
        )
    except OperationalError as exc:
        await session.execute(
            text("UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"),
            {"err": f"task queue unavailable: {exc}", "id": backtest_id},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc

    return {
        "backtest_id": backtest_id,
        "strategy_id": request.strategy_id,
        "task_id": task_id,
        "status": "pending",
    }


@router.post("/batch", status_code=status.HTTP_201_CREATED)
async def submit_batch_backtest(
    req: Request,
    request: BatchBacktestRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user_id = _extract_user_id(req)

    strategies = await session.execute(
        text("SELECT id, name FROM strategies WHERE id = ANY(:ids) AND user_id = :user_id"),
        {"ids": request.strategy_ids, "user_id": user_id},
    )
    rows = strategies.mappings().all()
    found_ids = {r["id"] for r in rows}
    missing = set(request.strategy_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"strategies not found: {sorted(missing)}",
        )
    name_map = {r["id"]: r["name"] for r in rows}

    params_snapshot = {
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "initial_cash": request.initial_cash,
        "benchmark_code": request.benchmark_code,
        "config": request.config,
        "filters": {
            "exclude_st": bool(request.exclude_st),
            "exclude_loss_pe": bool(request.exclude_loss_pe),
        },
        "target": {
            "type": request.target_type,
            "value": request.target_value,
            "label": _target_label(request.target_type, request.target_value),
        },
    }

    backtest_ids: list[int] = []
    for sid in request.strategy_ids:
        bid = await _create_backtest_record(
            session, user_id, sid,
            request.start_date, request.end_date,
            request.initial_cash, request.benchmark_code,
            params_snapshot,
        )
        backtest_ids.append(bid)

    task_ids: list[str] = []
    for bid in backtest_ids:
        tid = uuid4().hex
        await session.execute(
            text("UPDATE backtest_results SET task_id = :task_id, status = 'pending' WHERE id = :id"),
            {"task_id": tid, "id": bid},
        )
        task_ids.append(tid)

    await session.commit()

    try:
        sigs = [run_backtest_task.si(backtest_id=bid) for bid in backtest_ids]
        chain(*sigs).apply_async()
    except OperationalError as exc:
        for bid in backtest_ids:
            await session.execute(
                text("UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"),
                {"err": f"task queue unavailable: {exc}", "id": bid},
            )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"task queue unavailable: {exc}",
        ) from exc

    return {
        "backtest_ids": backtest_ids,
        "task_ids": task_ids,
        "total": len(backtest_ids),
        "strategy_names": [name_map[sid] for sid in request.strategy_ids],
    }


@router.get("")
async def list_backtests(
    req: Request,
    strategy_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user_id = _extract_user_id(req)
    clauses = ["b.user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id, "limit": limit, "offset": offset}
    if strategy_id:
        clauses.append("b.strategy_id = :strategy_id")
        params["strategy_id"] = strategy_id

    where_sql = " AND ".join(clauses)
    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM backtest_results b WHERE {where_sql}"),
        params,
    )
    total = int(count_result.scalar_one())

    result = await session.execute(
        text(
            f"""
            SELECT b.id, b.strategy_id, b.task_id, b.start_date, b.end_date,
                   b.initial_cash, b.benchmark_code, b.total_return, b.annual_return,
                   b.sharpe_ratio, b.max_drawdown, b.annual_vol, b.win_rate,
                   b.trade_count, b.status, b.error_message, b.created_at,
                   b.started_at, b.finished_at, b.params_snapshot,
                   s.name AS strategy_name
            FROM backtest_results b
            LEFT JOIN strategies s ON s.id = b.strategy_id
            WHERE {where_sql}
            ORDER BY b.created_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    items = [_with_target_fields(dict(row)) for row in result.mappings().all()]
    return {"items": items, "total": total, "page": offset // limit + 1, "page_size": limit}


@router.get("/{backtest_id}")
async def get_backtest(
    backtest_id: int,
    req: Request,
    include_kline: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user_id = _extract_user_id(req)
    result = await session.execute(
        text(
            """
            SELECT b.id, b.strategy_id, b.task_id, b.start_date, b.end_date,
                   b.initial_cash, b.benchmark_code, b.params_snapshot,
                   b.total_return, b.annual_return, b.sharpe_ratio, b.max_drawdown,
                   b.annual_vol, b.win_rate, b.trade_count,
                   b.performance, b.trade_records, b.equity_curve,
                   b.status, b.error_message, b.created_at,
                   b.started_at, b.finished_at,
                   s.name AS strategy_name, s.source_code
            FROM backtest_results b
            LEFT JOIN strategies s ON s.id = b.strategy_id
            WHERE b.id = :id AND b.user_id = :user_id
            """
        ),
        {"id": backtest_id, "user_id": user_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="backtest not found")

    payload = _with_target_fields(dict(row))

    raw_trades = payload.get("trade_records")
    trades = raw_trades
    if isinstance(trades, str):
        trades = json.loads(trades)

    if trades:
        all_ts_codes = sorted({t.get("ts_code") for t in trades if t.get("ts_code")})
        if all_ts_codes:
            names_result = await session.execute(
                text(
                    """
                    SELECT ts_code, name FROM stock_basic
                    WHERE ts_code = ANY(:ts_codes)
                    """
                ),
                {"ts_codes": all_ts_codes},
            )
            payload["stock_names"] = {r["ts_code"]: r["name"] for r in names_result.mappings().all()}

            if include_kline:
                start_date = payload["start_date"]
                end_date = payload["end_date"]
                kline_data: dict[str, list[dict[str, Any]]] = {}
                for ts_code in all_ts_codes:
                    klines = await get_klines(session, ts_code, start_date, end_date)
                    kline_data[ts_code] = _serialize_kline_rows(klines)
                payload["kline_data"] = kline_data

    return payload


@router.get("/{backtest_id}/klines")
async def get_backtest_klines(
    backtest_id: int,
    req: Request,
    ts_code: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    user_id = _extract_user_id(req)
    result = await session.execute(
        text(
            """
            SELECT start_date, end_date
            FROM backtest_results
            WHERE id = :id AND user_id = :user_id
            """
        ),
        {"id": backtest_id, "user_id": user_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="backtest not found")

    klines = await get_klines(session, ts_code, row["start_date"], row["end_date"])
    return _serialize_kline_rows(klines)


@router.delete("/{backtest_id}")
async def delete_backtest(
    backtest_id: int,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user_id = _extract_user_id(req)
    result = await session.execute(
        text("DELETE FROM backtest_results WHERE id = :id AND user_id = :user_id"),
        {"id": backtest_id, "user_id": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="backtest not found")
    await session.commit()
    return {"status": "ok"}


@router.get("/{backtest_id}/status")
async def backtest_status(
    backtest_id: int,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """返回回测的真实 DB 状态（按整数 id）。

    Celery 任务会同步把终态（running/success/failed）写入 backtest_results，
    因此直接读该行，而不是查 Celery result backend（它的 key 是 uuid task_id，不是 DB id）。
    """
    user_id = _extract_user_id(req)
    row = (
        await session.execute(
            text(
                "SELECT id, status, total_return, error_message, finished_at "
                "FROM backtest_results WHERE id = :id AND user_id = :user_id"
            ),
            {"id": backtest_id, "user_id": user_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="backtest not found")

    return {
        "id": row["id"],
        "status": row["status"],
        "total_return": row["total_return"],
        "error_message": row["error_message"],
        "finished_at": str(row["finished_at"]) if row["finished_at"] is not None else None,
        "ready": row["status"] in ("success", "failed", "cancelled"),
    }


@router.post("/{backtest_id}/cancel", status_code=202)
async def cancel_backtest(
    backtest_id: str,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Cancel a running backtest by revoking the Celery task.

    - Looks up the DB record by `celery_task_id` (column name `task_id`)
      and transitions `status` from `running` to `cancelled`.
    - Calls `celery_app.control.revoke(task_id, terminate=True, signal='SIGTERM')`
      to abort the worker process.
    - Returns 404 if no backtest found with the given id, or 409 if it is already
      in a terminal state (success/failed/cancelled).
    """
    # `backtest_id` here is the Celery task_id (string), per the /status endpoint convention
    task_id = backtest_id
    user_id = _extract_user_id(req)

    row = (await session.execute(
        text(
            "SELECT id, status, user_id FROM backtest_results "
            "WHERE task_id = :task_id AND user_id = :user_id"
        ),
        {"task_id": task_id, "user_id": user_id},
    )).first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="backtest not found")
    if row.status in ("success", "failed", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"backtest already in terminal state: {row.status}",
        )

    # Revoke the Celery task — terminate=True sends SIGTERM to the worker process
    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception as exc:
        # Fall through to mark DB as cancelled even if revoke failed (e.g. broker down)
        pass

    await session.execute(
        text(
            "UPDATE backtest_results "
            "SET status = 'cancelled', finished_at = NOW(), updated_at = NOW() "
            "WHERE id = :id AND status = 'running'"
        ),
        {"id": row.id},
    )
    await session.commit()

    return {"status": "cancelled", "task_id": task_id}
