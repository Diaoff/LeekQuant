"""Simulation account CRUD."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.cost import AShareCostCalculator, FeeConfig, build_fee_config
from app.backtest.signals import SignalInput, apply_cn_rules, map_signal_to_action
from app.data.providers import DataProviderError
from app.realtime.models import RealtimeTick
from app.realtime.providers import EastMoneyRealtimeProvider
from app.sim.serialize import (
    MONEY_QUANT,
    _dec,
    _dict_value,
    _json,
    _money,
    _serialize_row,
    serialize_rows,
)

logger = logging.getLogger(__name__)

LOT_SIZE = 100
RATIO_QUANT = Decimal("0.00000001")

async def get_account_or_404(
    session: AsyncSession,
    account_id: int,
    user_id: int,
    *,
    for_update: bool = False,
) -> dict[str, Any]:
    suffix = " FOR UPDATE OF a" if for_update else ""
    result = await session.execute(
        text(
            f"""
            SELECT a.id, a.user_id, a.strategy_id, a.name, a.initial_cash, a.available_cash,
                   a.frozen_cash, a.total_asset, a.status, a.config, a.created_at, a.updated_at,
                   p.value AS user_trading_fee_config
            FROM sim_accounts a
            LEFT JOIN user_preferences p
              ON p.user_id = a.user_id AND p.key = 'trading_fee'
            WHERE a.id = :id AND a.user_id = :user_id
            {suffix}
            """
        ),
        {"id": account_id, "user_id": user_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return dict(row)

async def list_accounts(session: AsyncSession, user_id: int, status_filter: str | None = None) -> list[dict[str, Any]]:
    clauses = ["a.user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id}
    if status_filter:
        clauses.append("a.status = :status")
        params["status"] = status_filter
    result = await session.execute(
        text(
            f"""
            SELECT a.id, a.user_id, a.strategy_id, a.name, a.initial_cash,
                   a.available_cash, a.frozen_cash, a.total_asset, a.status,
                   a.config, a.created_at, a.updated_at, s.name AS strategy_name
            FROM sim_accounts a
            LEFT JOIN strategies s ON s.id = a.strategy_id
            WHERE {" AND ".join(clauses)}
            ORDER BY a.total_asset DESC, a.id DESC
            """
        ),
        params,
    )
    return serialize_rows(list(result.mappings().all()))

async def create_account(
    session: AsyncSession,
    *,
    user_id: int,
    name: str,
    initial_cash: Decimal,
    strategy_id: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if initial_cash <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="initial_cash must be positive")
    result = await session.execute(
        text(
            """
            INSERT INTO sim_accounts (
                user_id, strategy_id, name, initial_cash, available_cash,
                frozen_cash, total_asset, config
            ) VALUES (
                :user_id, :strategy_id, :name, :initial_cash, :initial_cash,
                0, :initial_cash, CAST(:config AS JSONB)
            )
            RETURNING id, user_id, strategy_id, name, initial_cash, available_cash,
                      frozen_cash, total_asset, status, config, created_at, updated_at
            """
        ),
        {
            "user_id": user_id,
            "strategy_id": strategy_id,
            "name": name,
            "initial_cash": _money(initial_cash),
            "config": _json(config),
        },
    )
    row = dict(result.mappings().one())
    await session.execute(
        text(
            """
            INSERT INTO sim_cash_flow (account_id, flow_type, amount, balance_after, remark)
            VALUES (:account_id, '充值', :amount, :amount, '模拟账户初始化')
            """
        ),
        {"account_id": row["id"], "amount": _money(initial_cash)},
    )
    await session.commit()
    return _serialize_row(row)

async def update_account(
    session: AsyncSession,
    *,
    account_id: int,
    user_id: int,
    name: str | None = None,
    strategy_id: int | None = None,
    strategy_id_provided: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = await get_account_or_404(session, account_id, user_id)
    new_name = name if name is not None else existing["name"]
    new_strategy_id = strategy_id if strategy_id_provided else existing["strategy_id"]
    new_config: dict[str, Any] | None = None
    if config is not None:
        cur_config = existing.get("config")
        if isinstance(cur_config, dict):
            merged = dict(cur_config)
            merged.update(config)
            new_config = merged
        else:
            new_config = config
    result = await session.execute(
        text(
            """
            UPDATE sim_accounts
            SET name = :name, strategy_id = :strategy_id,
                config = COALESCE(:config, config),
                updated_at = NOW()
            WHERE id = :id AND user_id = :user_id
            RETURNING id, user_id, strategy_id, name, initial_cash, available_cash,
                      frozen_cash, total_asset, status, config, created_at, updated_at
            """
        ),
        {
            "name": new_name,
            "strategy_id": new_strategy_id,
            "config": json.dumps(new_config) if new_config is not None else None,
            "id": account_id,
            "user_id": user_id,
        },
    )
    row = dict(result.mappings().one())
    await session.commit()
    return _serialize_row(row)

async def delete_account(
    session: AsyncSession,
    *,
    account_id: int,
    user_id: int,
) -> bool:
    await get_account_or_404(session, account_id, user_id)
    result = await session.execute(
        text("DELETE FROM sim_accounts WHERE id = :id AND user_id = :user_id"),
        {"id": account_id, "user_id": user_id},
    )
    await session.commit()
    return bool(result.rowcount)

async def list_child_rows(
    session: AsyncSession,
    *,
    user_id: int,
    account_id: int,
    table: str,
    order_by: str,
    limit: int = 100,
    include_stock_name: bool = False,
) -> list[dict[str, Any]]:
    await get_account_or_404(session, account_id, user_id)
    limit = min(max(limit, 1), 500)
    select_clause = "t.*, COALESCE(sb.name, t.ts_code) AS stock_name" if include_stock_name else "t.*"
    join_clause = "LEFT JOIN stock_basic sb ON sb.ts_code = t.ts_code" if include_stock_name else ""
    result = await session.execute(
        text(
            f"""
            SELECT {select_clause}
            FROM {table} t
            {join_clause}
            WHERE t.account_id = :account_id
            ORDER BY {order_by}
            LIMIT :limit
            """
        ),
        {"account_id": account_id, "limit": limit},
    )
    return serialize_rows(list(result.mappings().all()))
