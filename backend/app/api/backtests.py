"""Backtest submission and status API endpoints."""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal
from uuid import uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.tasks import run_backtest_task
from app.data.stock_service import get_klines
from app.db.session import get_session
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

MARKET_TARGETS = {"主板", "创业板", "科创板", "北交所"}


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
    target_value: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "BacktestCreateRequest":
        if self.target_type == "all":
            self.target_value = None
            return self
        if self.target_type == "market":
            if self.target_value not in MARKET_TARGETS:
                raise ValueError("target_value must be one of 主板 / 创业板 / 科创板 / 北交所")
            return self
        if not self.target_value or not self.target_value.strip():
            raise ValueError("target_value is required for watchlist_group")
        self.target_value = self.target_value.strip()
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


def _target_label(target_type: str | None, target_value: str | None) -> str:
    if target_type == "market" and target_value:
        return target_value
    if target_type == "watchlist_group" and target_value:
        return f"自选股/{target_value}"
    return "全市场"


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
        text(
            """
            SELECT id
            FROM strategies
            WHERE id = :id AND user_id = :user_id
            """
        ),
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
        "target": {
            "type": request.target_type,
            "value": request.target_value,
            "label": _target_label(request.target_type, request.target_value),
        },
    }

    result = await session.execute(
        text(
            """
            INSERT INTO backtest_results (
                user_id, strategy_id, start_date, end_date,
                initial_cash, benchmark_code, params_snapshot, task_id
            ) VALUES (
                :user_id, :strategy_id, :start_date, :end_date,
                :initial_cash, :benchmark_code, CAST(:params AS JSONB), :task_id
            )
            RETURNING id, strategy_id, status, created_at
            """
        ),
        {
            "user_id": user_id,
            "strategy_id": request.strategy_id,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "initial_cash": request.initial_cash,
            "benchmark_code": request.benchmark_code,
            "params": json.dumps(params_snapshot, ensure_ascii=False, default=str),
            "task_id": "",
        },
    )
    bt_row = dict(result.mappings().one())
    backtest_id = bt_row["id"]

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
            text(
                "UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"
            ),
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


@router.get("")
async def list_backtests(
    req: Request,
    strategy_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    user_id = _extract_user_id(req)
    clauses = ["b.user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id}
    if strategy_id:
        clauses.append("b.strategy_id = :strategy_id")
        params["strategy_id"] = strategy_id

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
            WHERE {" AND ".join(clauses)}
            ORDER BY b.created_at DESC
            LIMIT 50
            """
        ),
        params,
    )
    return [_with_target_fields(dict(row)) for row in result.mappings().all()]


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

    if include_kline and payload.get("trade_records"):
        trades = payload["trade_records"]
        if isinstance(trades, str):
            trades = json.loads(trades)

        ts_codes = sorted({t.get("ts_code") for t in trades if t.get("ts_code")})
        if ts_codes:
            start_date = payload["start_date"]
            end_date = payload["end_date"]
            kline_data: dict[str, list[dict[str, Any]]] = {}
            for ts_code in ts_codes:
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
async def backtest_status(backtest_id: str) -> dict[str, Any]:
    async_result = AsyncResult(backtest_id, app=celery_app)
    payload = {
        "task_id": backtest_id,
        "status": async_result.status.lower(),
        "ready": async_result.ready(),
    }
    if async_result.ready():
        if async_result.failed():
            payload["error"] = str(async_result.result)
        else:
            payload["result"] = async_result.result
    return payload
