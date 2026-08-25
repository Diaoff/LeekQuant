"""跷跷板实时闭环：把大盘状态切换应用到模拟盘账户。

设计要点
--------
- 复用 ``sim_accounts.config`` JSONB 存放两个开关，避免新增迁移：
  - ``seesaw_enabled``：是否启用本账户的自动切换（默认 ``false``，需显式开启 → “仅推荐”）。
  - ``seesaw_mode``：账户当前的跷跷板模式，``"normal"``（权益/现金）或 ``"defensive"``（避险）。
- 进入 down：清掉账户现有权益持仓，等权买入整个避险库（与回测 ``defensive_pick_k=0``
  全池等权语义一致）。
- 恢复 up/neutral：把账户的避险持仓清回现金（不盲目回补策略仓位，避免破坏用户意图）。
- 切换通过 ``generate_order_from_signal(auto_match=True, auto_match_mode="close")``
  即时按当日收盘价撮合，复用既有 A 股规则（T+1、涨跌停、停牌过滤）。
- 默认所有账户 ``seesaw_enabled=false``，即“仅推荐/不执行”，即便部署上线也不会
  有任何账户自动交易，除非用户在账户配置中显式开启。
"""
from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.seesaw import rank_defensive_pool
from app.sim._helpers import SignalOrderRequest
from app.sim.orders import generate_order_from_signal

logger = logging.getLogger(__name__)


# ── 配置读取 ──────────────────────────────────────────────────────────────────


def _account_seesaw_enabled(config: dict[str, Any] | None) -> bool:
    cfg = config or {}
    return bool(cfg.get("seesaw_enabled", False))


def _account_seesaw_mode(config: dict[str, Any] | None) -> str:
    cfg = config or {}
    mode = cfg.get("seesaw_mode", "normal")
    return "defensive" if mode == "defensive" else "normal"


# ── 纯函数：切换方向决策（无 DB / 无副作用，便于单测） ──────────────────────────


def _decide_switch_direction(
    new_state: str,
    prev_state: str | None,
    current_mode: str,
) -> str | None:
    """根据大盘状态变迁与账户当前模式，决定切换方向。

    - 大盘首次转入 ``down`` 且账户处于 ``normal`` → ``"enter_defensive"``
    - 大盘恢复（``up``/``neutral``）且账户处于 ``defensive`` → ``"exit_defensive"``
    - 其余情况（含已处目标模式、未启用由调用方过滤）→ ``None``（不切换）

    用账户自身 ``seesaw_mode`` 作为权威守卫（而非全局状态历史），即使漏跑某次
    检测也不会重复切换或漏切。
    """
    if new_state == "down" and prev_state != "down" and current_mode == "normal":
        return "enter_defensive"
    if new_state in ("up", "neutral") and prev_state == "down" and current_mode == "defensive":
        return "exit_defensive"
    return None


# ── DB 读写 ───────────────────────────────────────────────────────────────────


async def _list_seesaw_accounts(session: AsyncSession) -> list[dict[str, Any]]:
    """返回所有启用跷跷板且状态为 active 的模拟账户。"""
    result = await session.execute(
        text(
            """
            SELECT id, user_id, config
            FROM sim_accounts
            WHERE status = 'active'
              AND config ->> 'seesaw_enabled' = 'true'
            """
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def _list_positions(session: AsyncSession, account_id: int) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT ts_code, shares, available_shares
            FROM sim_positions
            WHERE account_id = :aid AND shares > 0
            """
        ),
        {"aid": account_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def _set_account_seesaw_mode(session: AsyncSession, account_id: int, mode: str) -> None:
    """读取账户现有 config，合并 ``seesaw_mode`` 后写回（保留其他 config 字段）。"""
    result = await session.execute(
        text("SELECT config FROM sim_accounts WHERE id = :aid FOR UPDATE"),
        {"aid": account_id},
    )
    row = result.mappings().one_or_none()
    cfg = dict(row["config"]) if row and row["config"] else {}
    cfg["seesaw_mode"] = mode
    await session.execute(
        text("UPDATE sim_accounts SET config = CAST(:cfg AS JSONB), updated_at = NOW() WHERE id = :aid"),
        {"cfg": json.dumps(cfg, ensure_ascii=False), "aid": account_id},
    )


# ── 单账户切换执行 ─────────────────────────────────────────────────────────────


async def execute_seesaw_switch(
    session: AsyncSession,
    *,
    account: dict[str, Any],
    direction: str,
    pool_codes: list[str],
    trade_date: date,
    user_id: int,
) -> dict[str, Any]:
    """对单个账户执行一次跷跷板切换，返回执行摘要。"""
    account_id = int(account["id"])
    orders: list[dict[str, Any]] = []

    if direction == "enter_defensive":
        # 1) 清掉账户现有权益持仓
        for pos in await _list_positions(session, account_id):
            if int(pos.get("shares", 0)) <= 0:
                continue
            res = await generate_order_from_signal(
                session,
                user_id=user_id,
                account_id=account_id,
                request=SignalOrderRequest(
                    ts_code=pos["ts_code"],
                    signal_type="卖出",
                    trade_date=trade_date,
                    target_position=Decimal("0"),
                    reason="跷跷板避险：清仓权益持仓",
                ),
                auto_match=True,
                auto_match_mode="close",
            )
            orders.append(res)
        # 2) 等权买入整个避险库（与回测全池等权语义一致）
        n = len(pool_codes)
        if n > 0:
            target = (Decimal("1") / Decimal(n)).quantize(Decimal("0.0001"))
            for ts_code in pool_codes:
                res = await generate_order_from_signal(
                    session,
                    user_id=user_id,
                    account_id=account_id,
                    request=SignalOrderRequest(
                        ts_code=ts_code,
                        signal_type="买入",
                        trade_date=trade_date,
                        target_position=target,
                        reason="跷跷板避险：等权买入避险库",
                    ),
                    auto_match=True,
                    auto_match_mode="close",
                )
                orders.append(res)
        new_mode = "defensive"

    elif direction == "exit_defensive":
        # 清掉账户避险持仓，回现金（T+1 当日买入的部分由订单层自动 BLOCKED，次日再清）
        for pos in await _list_positions(session, account_id):
            if int(pos.get("shares", 0)) <= 0:
                continue
            res = await generate_order_from_signal(
                session,
                user_id=user_id,
                account_id=account_id,
                request=SignalOrderRequest(
                    ts_code=pos["ts_code"],
                    signal_type="卖出",
                    trade_date=trade_date,
                    target_position=Decimal("0"),
                    reason="跷跷板恢复：清仓避险持仓回现金",
                ),
                auto_match=True,
                auto_match_mode="close",
            )
            orders.append(res)
        new_mode = "normal"

    else:  # pragma: no cover - 调用方已过滤
        return {"account_id": account_id, "skipped": True, "reason": "unknown_direction"}

    await _set_account_seesaw_mode(session, account_id, new_mode)
    return {
        "account_id": account_id,
        "user_id": user_id,
        "direction": direction,
        "new_mode": new_mode,
        "orders": len(orders),
    }


# ── 编排：把大盘状态变迁应用到所有启用账户 ───────────────────────────────────────


async def apply_seesaw_transition(
    session: AsyncSession,
    *,
    new_state: str,
    prev_state: str | None,
    rules: Any,
    trade_date: date,
    account_lister: Callable[[AsyncSession], Awaitable[list[dict[str, Any]]]] | None = None,
    pool_loader: Callable[[AsyncSession, int], Awaitable[list[str]]] | None = None,
) -> dict[str, Any]:
    """检测大盘状态变迁，对所有启用跷跷板的模拟账户执行切换。

    依赖可注入（``account_lister`` / ``pool_loader``）以便单测；生产默认走
    :func:`_list_seesaw_accounts` 与 :func:`rank_defensive_pool`。
    """
    lister = account_lister or _list_seesaw_accounts
    loader = pool_loader or rank_defensive_pool

    accounts = await lister(session)
    switched: list[dict[str, Any]] = []
    skipped = 0

    for acc in accounts:
        user_id = int(acc["user_id"])
        current_mode = _account_seesaw_mode(acc.get("config"))
        direction = _decide_switch_direction(new_state, prev_state, current_mode)
        if direction is None:
            skipped += 1
            continue
        pool_codes = await loader(session, 200) if direction == "enter_defensive" else []
        result = await execute_seesaw_switch(
            session,
            account=acc,
            direction=direction,
            pool_codes=pool_codes,
            trade_date=trade_date,
            user_id=user_id,
        )
        switched.append(result)

    if switched:
        await session.commit()
        logger.info(
            "Seesaw sim switch applied: state=%s->%s, switched=%d, skipped=%d",
            prev_state, new_state, len(switched), skipped,
        )

    return {
        "new_state": new_state,
        "prev_state": prev_state,
        "switched": switched,
        "skipped": skipped,
    }
