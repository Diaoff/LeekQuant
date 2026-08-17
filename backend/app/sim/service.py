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

from app.sim._helpers import (
    SignalOrderRequest,
    _fee_config,
    _global_fee_config,
    _get_trade_calendar,
    _get_kline,
    _get_latest_kline_before_or_on,
    _get_position,
)

from app.sim.accounts import (
    get_account_or_404,
    list_accounts,
    create_account,
    update_account,
    delete_account,
    list_child_rows,
)

from app.sim.nav import (
    refresh_account_assets,
    refresh_position_market_values,
    check_stop_conditions,
    unlock_t1_positions,
    snapshot_daily_nav,
)

from app.sim.orders import (
    _resolve_match_price,
    _resolve_order_price_fallback,
    _limit_rate,
    _computed_limit_flags,
    _insert_signal,
    _strategy_signal_response,
    generate_order_from_signal,
    match_order,
    cancel_order,
)

from app.sim.valuation import (
    _realtime_ticks,
    _apply_realtime_position_values,
    _position_quote_codes,
    _position_today_baselines,
    _apply_position_today_pnl,
    _latest_nav_total_asset,
    _position_rows,
    _account_positions,
    enrich_account_with_realtime_valuation,
    list_accounts_with_realtime_valuation,
    get_account_with_realtime_valuation,
    list_positions_with_realtime_valuation,
)
