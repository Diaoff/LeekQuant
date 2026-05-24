"""Persistence helpers for user preferences."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.cost import FeeConfig, build_fee_config, fee_config_to_dict

TRADING_FEE_KEY = "trading_fee"


async def get_preference(
    session: AsyncSession,
    *,
    user_id: int,
    key: str,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT value
            FROM user_preferences
            WHERE user_id = :user_id AND key = :key
            """
        ),
        {"user_id": user_id, "key": key},
    )
    row = result.mappings().one_or_none()
    value = row["value"] if row else None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


async def set_preference(
    session: AsyncSession,
    *,
    user_id: int,
    key: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    await session.execute(
        text(
            """
            INSERT INTO user_preferences (user_id, key, value, updated_at)
            VALUES (:user_id, :key, CAST(:value AS JSONB), NOW())
            ON CONFLICT (user_id, key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = NOW()
            """
        ),
        {"user_id": user_id, "key": key, "value": json.dumps(value, ensure_ascii=False, default=str)},
    )
    await session.commit()
    return value


async def get_trading_fee_config(session: AsyncSession, user_id: int) -> FeeConfig:
    stored = await get_preference(session, user_id=user_id, key=TRADING_FEE_KEY)
    return build_fee_config(stored)


async def get_trading_fee_payload(session: AsyncSession, user_id: int) -> dict[str, str | bool]:
    return fee_config_to_dict(await get_trading_fee_config(session, user_id))


async def save_trading_fee_payload(
    session: AsyncSession,
    *,
    user_id: int,
    payload: dict[str, Any],
) -> dict[str, str | bool]:
    fee_config = build_fee_config(payload)
    normalized = fee_config_to_dict(fee_config)
    await set_preference(session, user_id=user_id, key=TRADING_FEE_KEY, value=normalized)
    return normalized
