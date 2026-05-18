"""5-level signal state machine.

Maps signal types (买入/增持/减仓/卖出/观望) to actual actions
based on current position ratio. Includes A-share rule filtering
(T+1, price limits, suspension).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActionType = Literal["BUY", "SELL_PARTIAL", "SELL_ALL", "HOLD", "BLOCKED"]


@dataclass(slots=True)
class SignalInput:
    signal_type: str
    current_position: float
    target_position: float | None = None


@dataclass(slots=True)
class SignalOutput:
    action: ActionType
    target_position: float
    reason: str = ""


def map_signal_to_action(signal: SignalInput) -> SignalOutput:
    """Map a 5-level signal to a concrete action.

    | Signal | Empty | Low | Mid | Full |
    |--------|-------|-----|-----|------|
    | 买入   | BUY→1.0 | BUY→target | BUY→target | HOLD |
    | 增持   | BUY→0.5 | BUY→cur+0.25 | HOLD | HOLD |
    | 减仓   | HOLD | HOLD | SELL_PARTIAL→cur-0.25 | SELL_PARTIAL→cur-0.25 |
    | 卖出   | HOLD | SELL_ALL | SELL_ALL | SELL_ALL |
    | 观望   | HOLD | HOLD | HOLD | HOLD |
    """
    s = signal.signal_type
    cur = signal.current_position

    if s == "观望":
        return SignalOutput("HOLD", cur)

    if cur <= 0:
        if s in ("买入", "增持"):
            target = signal.target_position or (1.0 if s == "买入" else 0.5)
            return SignalOutput("BUY", target)
        return SignalOutput("HOLD", 0.0)

    if s == "买入":
        target = signal.target_position or 1.0
        if target > cur:
            return SignalOutput("BUY", min(target, 1.0))
        return SignalOutput("HOLD", cur)

    if s == "增持":
        target = signal.target_position or min(cur + 0.25, 1.0)
        if target > cur:
            return SignalOutput("BUY", target)
        return SignalOutput("HOLD", cur)

    if s == "减仓":
        target = signal.target_position if signal.target_position is not None else max(cur - 0.25, 0.0)
        if target < cur:
            return SignalOutput("SELL_PARTIAL", target)
        return SignalOutput("HOLD", cur)

    if s == "卖出":
        return SignalOutput("SELL_ALL", 0.0)

    return SignalOutput("HOLD", cur)


def apply_cn_rules(
    action: ActionType,
    *,
    is_suspended: bool,
    is_limit_up: bool,
    is_limit_down: bool,
    is_t1_blocked: bool = False,
) -> tuple[ActionType, str]:
    """Apply A-share trading rules to filter actions.

    Returns (possibly modified action, reason).
    """
    if is_suspended:
        return "BLOCKED", "停牌"

    if action == "BUY" and is_limit_up:
        return "BLOCKED", "涨停不可买入"

    if action in ("SELL_ALL", "SELL_PARTIAL") and is_limit_down:
        return "BLOCKED", "跌停不可卖出"

    if action.startswith("SELL") and is_t1_blocked:
        return "BLOCKED", "T+1 当日买入不可卖出"

    return action, ""
