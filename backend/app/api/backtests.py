"""Backtest submission and status API endpoints."""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.tasks import run_backtest_task
from app.db.session import get_session
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


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


class BacktestCreateRequest(BaseModel):
    strategy_id: int
    pool_id: int | None = None
    start_date: date
    end_date: date
    initial_cash: float = 100000
    benchmark_code: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


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
            pool_id=None,
            start_date=s_date,
            end_date=e_date,
            initial_cash=initial_cash,
            benchmark_code=None,
            config={},
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
            SELECT id, user_id, name, source_code, pool_id, config
            FROM strategies
            WHERE id = :id AND user_id = :user_id
            """
        ),
        {"id": request.strategy_id, "user_id": user_id},
    )
    row = strategy.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")

    result = await session.execute(
        text(
            """
            INSERT INTO backtest_results (
                user_id, strategy_id, pool_id, start_date, end_date,
                initial_cash, benchmark_code, params_snapshot, task_id
            ) VALUES (
                :user_id, :strategy_id, :pool_id, :start_date, :end_date,
                :initial_cash, :benchmark_code, CAST(:params AS JSONB), :task_id
            )
            RETURNING id, strategy_id, status, created_at
            """
        ),
        {
            "user_id": user_id,
            "strategy_id": request.strategy_id,
            "pool_id": request.pool_id or row["pool_id"],
            "start_date": request.start_date,
            "end_date": request.end_date,
            "initial_cash": request.initial_cash,
            "benchmark_code": request.benchmark_code,
            "params": '{"start_date": "' + str(request.start_date) + '", "end_date": "' + str(request.end_date) + '", "initial_cash": ' + str(request.initial_cash) + '}',
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
            SELECT b.id, b.strategy_id, b.pool_id, b.task_id, b.start_date, b.end_date,
                   b.initial_cash, b.benchmark_code, b.total_return, b.annual_return,
                   b.sharpe_ratio, b.max_drawdown, b.annual_vol, b.win_rate,
                   b.trade_count, b.status, b.error_message, b.created_at,
                   b.started_at, b.finished_at,
                   s.name AS strategy_name, p.name AS pool_name
            FROM backtest_results b
            LEFT JOIN strategies s ON s.id = b.strategy_id
            LEFT JOIN stock_pools p ON p.id = b.pool_id
            WHERE {" AND ".join(clauses)}
            ORDER BY b.created_at DESC
            LIMIT 50
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


@router.get("/{backtest_id}")
async def get_backtest(
    backtest_id: int,
    req: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user_id = _extract_user_id(req)
    result = await session.execute(
        text(
            """
            SELECT b.id, b.strategy_id, b.pool_id, b.task_id, b.start_date, b.end_date,
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
    return dict(row)


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
