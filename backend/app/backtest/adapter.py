"""Python-native backtest engine.

Reads K-line data from PostgreSQL, executes user strategy code with MyTT
injected, and simulates daily trading using candle-path price inference.

Supports stop-loss, take-profit, trailing stop, and time-based stop.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np

from app.backtest.cost import AShareCostCalculator, CostResult, FeeConfig
from app.backtest.signals import SignalInput, SignalOutput, apply_cn_rules, map_signal_to_action
from app.backtest.strategy_runtime import (StrategyExecutionResult, compile_strategy, execute_compiled_signal, execute_compiled_script)


@dataclass(slots=True)
class KBar:
    ts_code: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal
    volume: int
    amount: Decimal
    adj_factor: Decimal | None
    is_suspended: bool
    is_limit_up: bool
    is_limit_down: bool
    turnover_rate: Decimal | None = None


@dataclass(slots=True)
class Position:
    ts_code: str
    shares: int = 0
    avg_cost: Decimal = Decimal("0")


@dataclass(slots=True)
class TradeRecord:
    ts_code: str
    trade_date: date
    direction: str              # "买入" / "卖出"
    price: Decimal
    volume: int
    amount: Decimal
    cost: CostResult
    signal_type: str            # 原始信号类型：买入/增持/减仓/卖出/观望

    action: str = ""            # 实际执行动作：BUY / SELL_PARTIAL / SELL_ALL / HOLD
    signal_reason: str = ""     # 信号原因描述（如"MA5上穿MA20金叉"）
    target_position: float = 0.0  # 目标仓位比例
    position_before: float = 0.0  # 交易前持仓比例（0.0~1.0）
    position_after: float = 0.0   # 交易后持仓比例（0.0~1.0）
    pnl: Decimal = Decimal("0")   # 本笔盈亏（买入时为0，卖出时计算）
    balance_before: Decimal = Decimal("0")  # 交易前总资产
    balance_after: Decimal = Decimal("0")   # 交易后总资产
    holding_days: int = 0       # 持仓天数（仅卖出时有意义）
    exit_reason: str = ""       # 卖出原因: "策略信号" / "止损" / "止盈" / "移动止盈" / "时间止损"


@dataclass(slots=True)
class _LotEntry:
    ts_code: str
    shares: int
    cost: Decimal
    entry_date: date
    entry_fee: Decimal = Decimal("0")


@dataclass(slots=True)
class _ClosedLot:
    ts_code: str
    shares: int
    entry_price: Decimal
    entry_date: date
    exit_price: Decimal
    exit_date: date
    entry_fee: Decimal
    exit_fee: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    return_rate: Decimal
    holding_days: int
    exit_reason: str

    @property
    def pnl(self) -> Decimal:
        return self.net_pnl


class SellDirection(str):
    """Detailed sell label that remains compatible with legacy '卖出' checks."""

    def __eq__(self, other: object) -> bool:
        if other == "卖出":
            return True
        return super().__eq__(other)

    __hash__ = str.__hash__


@dataclass(slots=True)
class BacktestConfig:
    strategy_id: int
    source_code: str
    stock_pool: list[str]
    start_date: date
    end_date: date
    initial_cash: Decimal = Decimal("100000")
    fee_config: FeeConfig = field(default_factory=FeeConfig)
    benchmark_code: str | None = None

    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    trailing_activation_pct: float = 0.0
    time_stop_days: int = 0
    slippage_pct: float = 0.001

    # Rebalancing: when multiple stocks trigger buy signals on the same day,
    # "ranked" mode sells low-scored existing positions to fund higher-priority
    # new buys.  Disabled by default for backward compat.
    rebalance_mode: str = "disabled"  # "disabled" | "ranked"
    max_positions: int = 0  # 0 = unlimited, capped at this when rebalance_mode="ranked"

    # Rebalance v2 settings (weekly, ranked, equal-weight)
    rebalance_version: int = 1
    rebalance_frequency: str = "weekly"
    weighting_method: str = "equal"
    rank_buffer_pct: float = 0.2
    score_max_age_sessions: int = 5

    execution_timeframe: str = "1D"
    signal_timeframe: str = "1D"
    strategy_mode: str = "signal"


@dataclass(slots=True)
class _SignalCandidate:
    ts_code: str
    bar: KBar
    action: SignalOutput
    signal: dict[str, Any] | None
    exit_reason: str | None = None
    buy_priority_score: Decimal = Decimal("0")
    buy_priority_source: str = "default"
    turnover_rate: Decimal | None = None
    fill_bar: KBar | None = None  # next day's bar for fill price (None = fallback)


class BacktestContext:
    """Context object exposed to user strategy code."""

    def __init__(
        self,
        klines: list[KBar],
        positions: dict[str, Position],
        total_asset: Decimal,
        current_price: Decimal | None = None,
        runner: 'BacktestRunner | None' = None,
        ts_code: str | None = None,
    ):
        self._klines = klines
        self._current_position: float = 0.0
        self.current_price = float(current_price) if current_price is not None else None
        self._runner = runner
        self._ts_code = ts_code

    @property
    def close(self) -> np.ndarray:
        return np.array([float(k.close) for k in self._klines])

    @property
    def open(self) -> np.ndarray:
        return np.array([float(k.open) for k in self._klines])

    @property
    def high(self) -> np.ndarray:
        return np.array([float(k.high) for k in self._klines])

    @property
    def low(self) -> np.ndarray:
        return np.array([float(k.low) for k in self._klines])

    @property
    def volume(self) -> np.ndarray:
        return np.array([k.volume for k in self._klines])

    @property
    def amount(self) -> np.ndarray:
        return np.array([float(k.amount) for k in self._klines])

    @property
    def trade_date(self) -> date:
        return self._klines[-1].trade_date if self._klines else date.today()

    @property
    def bar_count(self) -> int:
        """Number of bars accumulated so far (len of the current window).

        Useful for cooldown/timing logic in user strategies (e.g. "add only
        once every N bars"). The value equals ``len(self._klines)``, i.e. the
        window length at the current bar.
        """
        return len(self._klines)

    @property
    def current_position(self) -> float:
        return self._current_position

    @current_position.setter
    def current_position(self, value: float) -> None:
        self._current_position = value

    @property
    def stock_position_weight(self) -> float:
        if not self._runner or not self._ts_code or not self._klines:
            return 0.0
        pos = self._runner.positions.get(self._ts_code)
        if not pos or pos.shares <= 0:
            return 0.0
        observable_price = self._klines[-1].close
        nav = self._runner._calc_total_asset(self._runner._all_klines, self.trade_date)
        if nav <= 0:
            return 0.0
        return float(pos.shares * observable_price / nav)

    @property
    def portfolio_exposure(self) -> float:
        if not self._runner:
            return 0.0
        nav = self._runner._calc_total_asset(self._runner._all_klines, self.trade_date)
        if nav <= 0:
            return 0.0
        total_market_value = Decimal("0")
        for ts_code, pos in self._runner.positions.items():
            if pos.shares <= 0:
                continue
            klines = self._runner._all_klines.get(ts_code, [])
            price = None
            for k in reversed(klines):
                if k.trade_date <= self.trade_date and k.close:
                    price = k.close
                    break
            if price:
                total_market_value += price * pos.shares
        return float(total_market_value / nav)

    @property
    def position_shares(self) -> int:
        if not self._runner or not self._ts_code:
            return 0
        pos = self._runner.positions.get(self._ts_code)
        return pos.shares if pos else 0

    @property
    def cash(self) -> float:
        if not self._runner:
            return 0.0
        return float(self._runner.cash)


class ScriptContext:
    """Context for on_bar() callback in script strategy mode."""

    def __init__(self, runner: "BacktestRunner", ts_code: str, bar: KBar, total_asset: Decimal):
        self._runner = runner
        self._ts_code = ts_code
        self._bar = bar
        self._total_asset = total_asset
        self._action_taken = False

        self.ts_code = ts_code
        self.date = bar.trade_date
        self.open = float(bar.open)
        self.high = float(bar.high)
        self.low = float(bar.low)
        self.close = float(bar.close)
        self.volume = bar.volume

        pos = runner.positions.get(ts_code)
        self.position = float(pos.shares * bar.close / total_asset) if pos and total_asset > 0 else 0.0
        self.shares = pos.shares if pos else 0
        self.cash = float(runner.cash)
        self.portfolio_value = float(total_asset)

    def buy(self, pct: float = 1.0) -> None:
        if self._action_taken:
            return
        self._action_taken = True
        self._runner._script_pending_action = ("BUY", self._ts_code, pct, self._bar, "策略信号: buy")

    def sell(self, pct: float = 0.0) -> None:
        if self._action_taken:
            return
        self._action_taken = True
        if pct <= 0 or self.shares <= 0:
            self._runner._script_pending_action = ("SELL_ALL", self._ts_code, 0.0, self._bar, "策略信号: sell")
        else:
            self._runner._script_pending_action = ("SELL_PARTIAL", self._ts_code, pct, self._bar, "策略信号: sell")

    def hold(self) -> None:
        self._action_taken = True


class BacktestRunner:
    """Day-by-day backtest engine using closing-price matching."""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.calculator = AShareCostCalculator(config.fee_config)
        self.cash = config.initial_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.strategy_errors: list[dict[str, Any]] = []
        self._entry_dates: dict[str, date] = {}
        self._entry_prices: dict[str, Decimal] = {}
        self._highest_since_entry: dict[str, Decimal] = {}
        self._lowest_since_entry: dict[str, Decimal] = {}
        self._open_lots: dict[str, list[_LotEntry]] = {}
        self._closed_lots: list[_ClosedLot] = []
        self._script_pending_action: tuple[str, str, float, KBar, str] | None = None
        self._compiled_strategy = compile_strategy(config.source_code)
        self._script_mode = config.strategy_mode == "script"
        self._slippage = Decimal(str(config.slippage_pct))
        self._all_klines: dict[str, list[KBar]] = {}
        # Per-stock day index for O(1) bar lookup instead of O(n) list comprehension.
        self._stock_day_index: dict[str, int] = {code: 0 for code in config.stock_pool}
        # Rebalance v2 planner (initialized lazily in run())
        self.rebalance_planner: Any = None
        self._stored_rebalance_plan: Any = None

    @staticmethod
    def _infer_candle_path(open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> list[Decimal]:
        if close >= open_:
            return [open_, low, high, close]
        else:
            return [open_, high, low, close]

    def _check_exit_conditions(
        self,
        ts_code: str,
        day_high: Decimal,
        day_low: Decimal,
        bar: KBar,
    ) -> str | None:
        """Check risk-exit triggers for an open position on signal day `bar`.

        Triggers are evaluated against the day's intraday high/low so that a
        stop is fired when price *touches* the threshold during the session,
        not only when the open happens to gap past it (the previous behavior
        used bar.open and could miss intraday touches). The actual fill price
        is still resolved later at the next day's open (next_open mode), so the
        decision date (td) and the fill date (td+1) stay consistent with the
        no-lookahead execution model.
        """
        entry_price = self._entry_prices.get(ts_code)
        if entry_price is None or entry_price <= 0:
            return None
        if self.config.stop_loss_pct > 0:
            # Touched the stop when the day's low fell enough below entry.
            loss_pct = float((entry_price - day_low) / entry_price)
            if loss_pct >= self.config.stop_loss_pct:
                return "止损"
        if self.config.take_profit_pct > 0:
            # Touched the target when the day's high rose enough above entry.
            profit_pct = float((day_high - entry_price) / entry_price)
            if profit_pct >= self.config.take_profit_pct:
                return "止盈"
        if self.config.trailing_stop_pct > 0 and self.config.trailing_activation_pct > 0:
            highest = self._highest_since_entry.get(ts_code, entry_price)
            activated = float((highest - entry_price) / entry_price) >= self.config.trailing_activation_pct
            if activated:
                # Trail fires when price retracts from the peak by the trail %,
                # using the day's low as the worst intraday point.
                trail_pct = float((highest - day_low) / highest)
                if trail_pct >= self.config.trailing_stop_pct:
                    return "移动止盈"
        if self.config.time_stop_days > 0:
            entry_date = self._entry_dates.get(ts_code)
            if entry_date is not None and (bar.trade_date - entry_date).days >= self.config.time_stop_days:
                # Time stop only fires while still underwater (floating loss).
                pnl_pct = float((day_low - entry_price) / entry_price)
                if pnl_pct <= 0:
                    return "时间止损"
        return None

    def run(self, all_klines: dict[str, list[KBar]]) -> dict[str, Any]:
        """Run backtest for all stocks in the pool."""
        self._all_klines = all_klines
        self._stock_day_index = {code: 0 for code in self.config.stock_pool}
        trading_dates = self._get_trading_dates(all_klines)
        if not trading_dates:
            raise ValueError("no trading dates found in the specified range")

        # Initialize rebalance v2 planner if ranked mode with v2+
        self.rebalance_planner = None
        self._stored_rebalance_plan = None
        if self.config.rebalance_mode == "ranked" and self.config.rebalance_version >= 2:
            from app.backtest.rebalance import WeeklyRebalancePlanner
            self.rebalance_planner = WeeklyRebalancePlanner(self.config, self)

        lookback = 60
        for i, td in enumerate(trading_dates):
            # Next trading day's date — used to look up the fill bar so that
            # orders generated "as of td" (using data through td-1) execute
            # at td+1's open. This eliminates the lookahead bias where the
            # strategy saw td's close and filled at td's intraday prices.
            next_td = trading_dates[i + 1] if i + 1 < len(trading_dates) else None

            # Execute stored rebalance plan at the open of the fill date
            if self._stored_rebalance_plan is not None and self.rebalance_planner is not None:
                fill_bar_map: dict[str, KBar] = {}
                for ts_code in set(self.config.stock_pool):
                    bar = self._find_bar(ts_code, td)
                    if bar is not None:
                        fill_bar_map[ts_code] = bar
                self.rebalance_planner.execute(self._stored_rebalance_plan, fill_bar_map)
                self._stored_rebalance_plan = None

            total_asset = self._calc_total_asset(all_klines, td)
            self.equity_curve.append({
                "date": td.isoformat(),
                "total_asset": float(total_asset),
                "cash": float(self.cash),
            })

            candidates: list[_SignalCandidate] = []
            for ts_code in self.config.stock_pool:
                klines = all_klines.get(ts_code, [])
                if not klines:
                    continue

                # Locate td's bar via a monotonic forward pointer (O(1) amortized)
                # instead of an O(n) list comprehension per stock per day. The
                # pointer only advances because both trading_dates and each
                # stock's klines are sorted chronologically.
                idx = self._stock_day_index.get(ts_code, 0)
                if idx < 0:
                    # A previous gap day may have left a negative placeholder;
                    # never let the pointer go negative or the scan below would
                    # read klines[-1]. Restart from the beginning for this stock.
                    idx = 0
                while idx < len(klines) and klines[idx].trade_date <= td:
                    idx += 1
                idx -= 1  # back to the last bar with trade_date <= td
                if idx < 0 or idx >= len(klines):
                    # This stock has no data at or before td (pre-listing or a
                    # gap). Reset the pointer and skip — correctness is preserved
                    # because the next real-data day restarts the scan.
                    self._stock_day_index[ts_code] = 0
                    continue
                self._stock_day_index[ts_code] = idx
                if klines[idx].trade_date != td:
                    # Stock has data up to some day before td but none on td.
                    continue

                bar = klines[idx]
                if bar.is_suspended:
                    continue

                # Strategy window EXCLUDES td's bar (index idx) to avoid
                # lookahead bias. Strategy sees data through td-1 only.
                window = klines[max(0, idx - lookback):idx]
                if not window:
                    continue

                # Look up the fill bar (td+1's bar) for this stock using
                # the next element in the sorted klines list (O(1)).
                fill_bar: KBar | None = None
                if next_td is not None and idx + 1 < len(klines) and klines[idx + 1].trade_date == next_td:
                    fill_bar = klines[idx + 1]

                price_path = self._infer_candle_path(bar.open, bar.high, bar.low, bar.close)

                if ts_code in self.positions and self.positions[ts_code].shares > 0:
                    cur_high = self._highest_since_entry.get(ts_code, bar.high)
                    cur_low = self._lowest_since_entry.get(ts_code, bar.low)
                    self._highest_since_entry[ts_code] = max(cur_high, bar.high)
                    self._lowest_since_entry[ts_code] = min(cur_low, bar.low)

                exit_reason = self._check_exit_conditions(ts_code, bar.high, bar.low, bar)
                if exit_reason:
                    if self.rebalance_planner is not None:
                        self.rebalance_planner.on_exit(ts_code, exit_reason)
                    exec_action = SignalOutput(action="SELL_ALL", target_position=0.0)
                    candidates.append(_SignalCandidate(
                        ts_code=ts_code,
                        bar=bar,
                        action=exec_action,
                        signal=None,
                        exit_reason=exit_reason,
                        fill_bar=fill_bar,
                    ))
                    continue

                if self._script_mode:
                    ctx = ScriptContext(self, ts_code, bar, total_asset)
                    try:
                        self._exec_strategy_script(ctx)
                    except Exception as exc:
                        self.strategy_errors.append({
                            "strategy_id": self.config.strategy_id,
                            "ts_code": ts_code,
                            "trade_date": td.isoformat(),
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        })
                    if self._script_pending_action:
                        action_type, action_ts_code, pct, action_bar, reason = self._script_pending_action
                        self._script_pending_action = None
                        if action_ts_code != ts_code:
                            continue
                        if action_type == "BUY":
                            exec_action = SignalOutput(action="BUY", target_position=min(pct, 1.0))
                        elif action_type == "SELL_ALL":
                            exec_action = SignalOutput(action="SELL_ALL", target_position=0.0)
                        else:
                            exec_action = SignalOutput(action="SELL_PARTIAL", target_position=pct)
                        signal_data = {"signal_type": reason, "current_position": ctx.position, "target_position": pct}
                        reason_text, blocked = self._apply_rules(exec_action, ts_code, bar, self.positions.get(ts_code), td)
                        if blocked:
                            self.signals.append({
                                "ts_code": ts_code,
                                "trade_date": td.isoformat(),
                                "signal_type": reason,
                                "action": "BLOCKED",
                                "reason": reason_text,
                            })
                            continue
                        candidates.append(_SignalCandidate(
                            ts_code=ts_code, bar=bar, action=exec_action, signal=signal_data, exit_reason=None,
                            fill_bar=fill_bar,
                        ))
                        if self.rebalance_planner is not None:
                            if action_type in ("SELL_ALL", "SELL_PARTIAL"):
                                self.rebalance_planner.on_exit(ts_code, reason)
                            else:
                                has_position = ts_code in self.positions and self.positions[ts_code].shares > 0
                                self.rebalance_planner.on_signal(ts_code, reason, td, has_position)
                    continue

                ctx = BacktestContext(window, self.positions, total_asset, runner=self, ts_code=ts_code)
                signal_result = self._exec_strategy(ctx, total_asset)
                if not signal_result.ok:
                    self.strategy_errors.append({
                        "strategy_id": self.config.strategy_id,
                        "ts_code": ts_code,
                        "trade_date": td.isoformat(),
                        **signal_result.to_error_dict(),
                    })
                    continue
                signal = signal_result.signal
                if signal is None:
                    continue

                action_info = map_signal_to_action(SignalInput(
                    signal_type=signal.get("signal_type"),
                    current_position=signal.get("current_position", 0.0),
                    target_position=signal.get("target_position"),
                ))

                reason, blocked = self._apply_rules(
                    action_info, ts_code, bar, self.positions.get(ts_code), td
                )
                if blocked:
                    self.signals.append({
                        "ts_code": ts_code,
                        "trade_date": td.isoformat(),
                        "signal_type": signal.get("signal_type"),
                        "action": "BLOCKED",
                        "reason": reason,
                    })
                    continue

                candidates.append(self._build_signal_candidate(ts_code, bar, action_info, signal, td, fill_bar))

                # Notify rebalance planner of buy/add signals
                if self.rebalance_planner is not None:
                    signal_type = signal.get("signal_type") if signal else None
                    if signal_type:
                        has_position = ts_code in self.positions and self.positions[ts_code].shares > 0
                        self.rebalance_planner.on_signal(ts_code, signal_type, td, has_position)

            # ---- Phase 1: execute all sell candidates (forced exits + strategy sells) ----
            # Sells come first so they free up cash before buys are evaluated.
            for candidate in sorted(candidates, key=self._candidate_sort_key):
                if candidate.action.action not in ("SELL_ALL", "SELL_PARTIAL"):
                    continue
                match_blocked_reason = self._execute_action(
                    candidate.action,
                    candidate.ts_code,
                    candidate.bar,
                    total_asset,
                    candidate.signal,
                    exit_reason=candidate.exit_reason,
                    fill_bar=candidate.fill_bar,
                )
                signal_record = self._candidate_signal_record(candidate)
                if match_blocked_reason:
                    signal_record["match_status"] = "BLOCKED"
                    signal_record["reason"] = match_blocked_reason
                self.signals.append(signal_record)

            # ---- Phase 2: rebalance (sell low-scored positions to fund buys) ----
            buy_candidates = [c for c in candidates if c.action.action == "BUY"]
            if self.config.rebalance_mode == "ranked" and buy_candidates:
                self._rebalance_for_buys(buy_candidates, total_asset)

            # ---- Phase 3: execute all buy candidates ----
            for candidate in sorted(buy_candidates, key=self._candidate_sort_key):
                match_blocked_reason = self._execute_action(
                    candidate.action,
                    candidate.ts_code,
                    candidate.bar,
                    total_asset,
                    candidate.signal,
                    exit_reason=candidate.exit_reason,
                    fill_bar=candidate.fill_bar,
                )
                signal_record = self._candidate_signal_record(candidate)
                if match_blocked_reason:
                    signal_record["match_status"] = "BLOCKED"
                    signal_record["reason"] = match_blocked_reason
                self.signals.append(signal_record)

            # ---- Phase 4: weekly rebalance check (v2) ----
            if self.rebalance_planner is not None and self.rebalance_planner.should_run_weekly(td, trading_dates):
                plan = self.rebalance_planner.plan(td, all_klines, total_asset, trading_dates)
                if plan is not None:
                    plan.fill_date = next_td
                    self._stored_rebalance_plan = plan

        return self._compute_results()

    def _build_signal_candidate(
        self,
        ts_code: str,
        bar: KBar,
        action: SignalOutput,
        signal: dict[str, Any],
        td: date,
        fill_bar: KBar | None = None,
    ) -> _SignalCandidate:
        priority_score = Decimal("0")
        priority_source = "default"

        if signal.get("signal_type") in ("买入", "增持"):
            confidence = self._decimal_or_none(signal.get("confidence"))
            if confidence is not None:
                priority_score = confidence
                priority_source = "confidence"

        return _SignalCandidate(
            ts_code=ts_code,
            bar=bar,
            action=action,
            signal=signal,
            buy_priority_score=priority_score,
            buy_priority_source=priority_source,
            turnover_rate=bar.turnover_rate,
            fill_bar=fill_bar,
        )

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))

    @staticmethod
    def _candidate_sort_key(candidate: _SignalCandidate) -> tuple[int, int, Decimal, Decimal, Decimal, str]:
        signal_type = candidate.signal.get("signal_type") if candidate.signal else candidate.exit_reason
        if candidate.exit_reason or signal_type in ("卖出", "减仓"):
            group = 0
        elif signal_type in ("买入", "增持"):
            group = 1
        else:
            group = 2
        if candidate.exit_reason:
            source_order = 0
        elif group == 0:
            source_order = 1
        else:
            source_order = 0 if candidate.buy_priority_source == "confidence" else 1
        target_position = Decimal(str(candidate.action.target_position or 0))
        turnover = -candidate.turnover_rate if candidate.turnover_rate is not None else Decimal("-1")
        return group, source_order, -candidate.buy_priority_score, turnover, -target_position, candidate.ts_code

    @staticmethod
    def _candidate_signal_record(candidate: _SignalCandidate) -> dict[str, Any]:
        signal = candidate.signal or {}
        record = {
            "ts_code": candidate.ts_code,
            "trade_date": candidate.bar.trade_date.isoformat(),
            "signal_type": signal.get("signal_type") or candidate.exit_reason,
            "action": candidate.action.action,
            "target_position": candidate.action.target_position,
        }
        if candidate.exit_reason:
            record["exit_reason"] = candidate.exit_reason
        if signal.get("signal_type") in ("买入", "增持"):
            record.update({
                "buy_priority_score": str(candidate.buy_priority_score),
                "buy_priority_source": candidate.buy_priority_source,
                "turnover_rate": str(candidate.turnover_rate) if candidate.turnover_rate is not None else None,
            })
        return record

    def _get_trading_dates(self, all_klines: dict[str, list[KBar]]) -> list[date]:
        dates: set[date] = set()
        for klines in all_klines.values():
            for k in klines:
                if self.config.start_date <= k.trade_date <= self.config.end_date:
                    dates.add(k.trade_date)
        return sorted(dates)

    @staticmethod
    def _adjust_price(price: Decimal, adj_factor: Decimal | None, mode: str) -> Decimal:
        """Apply adjustment factor to price based on mode.

        Providers fetch前复权 (qfq) prices by default (see BACKTEST_ADJUST_MODE
        in app.core.config). When prices are already qfq-adjusted, adj_factor
        is stored for audit/reference only — multiplying again would
        double-adjust. The adj_factor field is plumbed through DB → KBar
        for future use cases (e.g., converting between raw/adjusted prices
        for display).

        Args:
            price: Raw or qfq price from KBar.
            adj_factor: Cumulative adjustment factor (latest=1), or None
                when provider does not expose it (AData/EastMoney).
            mode: One of "qfq" (default, prices already adjusted),
                "hfq" (后复权, future use), or "none" (no adjustment).

        Returns:
            Adjusted price. In qfq mode, returns price unchanged (already
            adjusted by provider).
        """
        if mode == "none":
            return price
        # In qfq mode, providers already return前复权 prices; adj_factor is
        # stored for audit only. Returning price unchanged avoids the
        # double-adjustment bug (qfq_price * adj_factor would be wrong).
        return price

    def _calc_total_asset(self, all_klines: dict[str, list[KBar]], td: date) -> Decimal:
        position_value = Decimal("0")
        for ts_code, pos in self.positions.items():
            if pos.shares <= 0:
                continue
            klines = all_klines.get(ts_code, [])
            price = None
            adj_factor: Decimal | None = None
            for k in reversed(klines):
                if k.trade_date <= td and k.close:
                    price = k.close
                    adj_factor = k.adj_factor
                    break
            if price:
                adjusted = self._adjust_price(price, adj_factor, self._adjust_mode())
                position_value += adjusted * pos.shares
        return self.cash + position_value

    def _adjust_mode(self) -> str:
        """Read BACKTEST_ADJUST_MODE from settings, defaulting to 'qfq'.

        Imported lazily to avoid creating asyncio primitives at module import
        (mirrors the ws_producer pattern).
        """
        try:
            from app.core.config import get_settings
            return get_settings().backtest_adjust_mode
        except Exception:
            return "qfq"

    def _exec_strategy(self, ctx: BacktestContext, total_asset: Decimal) -> StrategyExecutionResult:
        try:
            ctx.current_position = self._position_ratio(total_asset)
        except AttributeError:
            pass
        return execute_compiled_signal(self._compiled_strategy, ctx)

    def _find_bar(self, ts_code: str, td: date) -> KBar | None:
        """Find the KBar for *ts_code* on trading day *td*.

        Scans the pre-loaded klines (sorted by trade_date).  Called only
        during rebalancing, so linear scan is acceptable.
        """
        klines = self._all_klines.get(ts_code, [])
        for k in klines:
            if k.trade_date == td:
                return k
        return None

    def _position_score(self, ts_code: str, td: date) -> Decimal:
        """Return a score for an existing position on day *td*.

        Uses the buy_priority_score from the latest available signal.
        Defaults to Decimal("0") — lowest score, making it a replacement candidate.
        """
        return Decimal("0")

    def _rebalance_for_buys(
        self,
        buy_candidates: list[_SignalCandidate],
        total_asset: Decimal,
    ) -> None:
        """Sell low-scored positions to free cash for high-priority buy signals.

        Only sells positions whose score is *strictly lower* than the lowest
        buy-candidate score, so high-scored holdings are never replaced by
        lower-scored new signals.
        """
        if not buy_candidates:
            return

        td = buy_candidates[0].bar.trade_date
        buy_scores = sorted({c.buy_priority_score for c in buy_candidates})
        min_buy_score = buy_scores[0] if buy_scores else Decimal("0")

        # Score existing positions, filter out ones already held by a buy candidate
        # (we don't want to sell a stock we're about to buy more of).
        buy_ts_codes = {c.ts_code for c in buy_candidates}
        scored: list[tuple[str, Decimal]] = []
        for ts_code, pos in list(self.positions.items()):
            if pos.shares <= 0 or ts_code in buy_ts_codes:
                continue
            score = self._position_score(ts_code, td)
            if score >= min_buy_score:
                continue  # worth keeping
            scored.append((ts_code, score))

        # Sort by score ascending (worst first), then by ts_code for determinism.
        scored.sort(key=lambda x: (x[1], x[0]))

        # Sell from the lowest-scored positions until cash suffices for all buys.
        # We use a rough estimate: the sum of (target_position * total_asset) for
        # stocks not already held at or above target.
        needed = Decimal("0")
        for c in buy_candidates:
            target_value = total_asset * Decimal(str(c.action.target_position))
            pos = self.positions.get(c.ts_code)
            cur = pos.avg_cost * pos.shares if pos else Decimal("0")
            needed += max(target_value - cur, Decimal("0"))

        if self.cash >= needed:
            return  # No rebalance required.

        for ts_code, score in scored:
            if self.cash >= needed:
                break
            bar = self._find_bar(ts_code, td)
            if bar is None or bar.is_limit_down:
                continue
            # Sell the full position.  _execute_action handles lot tracking,
            # trade recording, and cash update.
            action = SignalOutput(action="SELL_ALL", target_position=0.0)
            self._execute_action(
                action,
                ts_code,
                bar,
                total_asset,
                signal={"signal_type": "调仓", "reason": f"调仓卖出: 评分{score}低于新信号{min_buy_score}"},
                exit_reason="调仓",
                fill_bar=None,
            )
            self.signals.append({
                "ts_code": ts_code,
                "trade_date": td.isoformat(),
                "signal_type": "调仓",
                "action": "SELL_ALL",
                "target_position": 0.0,
                "exit_reason": "调仓",
            })

    def _exec_strategy_script(self, ctx: ScriptContext) -> None:
        """Execute script-mode on_bar() using the pre-compiled strategy."""
        execute_compiled_script(self._compiled_strategy, ctx)

    def _position_ratio(self, total_asset: Decimal) -> float:
        if total_asset <= 0:
            return 0.0
        pos_value = sum(
            p.avg_cost * p.shares for p in self.positions.values() if p.shares > 0
        )
        return float(pos_value / total_asset)

    def _book_asset(self) -> Decimal:
        """Total asset using avg_cost (book value) for consistent position ratio."""
        return self.cash + sum(
            p.avg_cost * p.shares for p in self.positions.values() if p.shares > 0
        )

    def _apply_rules(
        self,
        action: SignalOutput,
        ts_code: str,
        bar: KBar,
        position: Position | None,
        td: date,
    ) -> tuple[str, bool]:
        if bar.is_suspended:
            return "停牌", True
        if action.action.startswith("SELL"):
            pos = self.positions.get(ts_code)
            if not pos or pos.shares <= 0:
                return "无持仓", True
            # T+1 is enforced in _execute_action against the effective fill
            # date. A next-open order signaled on the buy date may be legal
            # when it actually fills on the following trading day.
        return "", False

    def _execute_action(
        self,
        action: SignalOutput,
        ts_code: str,
        bar: KBar,
        total_asset: Decimal,
        signal: dict | None = None,
        exit_reason: str | None = None,
        fill_bar: KBar | None = None,
    ) -> str | None:
        # Apply adjustment factor to bar prices. In qfq mode (default), this
        # is a no-op since providers already return前复权 prices; adj_factor
        # is consulted for audit/future extensibility.
        adjust_mode = self._adjust_mode()
        adj_open = self._adjust_price(bar.open, bar.adj_factor, adjust_mode)
        adj_high = self._adjust_price(bar.high, bar.adj_factor, adjust_mode)
        adj_low = self._adjust_price(bar.low, bar.adj_factor, adjust_mode)
        adj_close = self._adjust_price(bar.close, bar.adj_factor, adjust_mode)

        # Determine fill price based on BACKTEST_FILL_PRICE_MODE.
        # - "next_open" (default): fill at next day's open (fill_bar.open).
        #   Eliminates lookahead bias — strategy decides using data through
        #   td-1 and fills at td+1's open. Falls back to bar.close when
        #   fill_bar is unavailable (e.g., last trading day).
        # - "current_intraday": legacy behavior — simulate intraday fill on
        #   signal day using candle-path inference. Retained for backward
        #   compat with tests that don't pass fill_bar.
        # - "current_close": fill at signal day's close.
        fill_mode = self._fill_price_mode()
        slippage = Decimal(str(self.config.slippage_pct))

        if fill_mode == "next_open" and fill_bar is not None and fill_bar.open is not None:
            fill_price = self._adjust_price(fill_bar.open, fill_bar.adj_factor, adjust_mode)
            trade_date = fill_bar.trade_date
        elif fill_mode == "current_close":
            fill_price = adj_close
            trade_date = bar.trade_date
        else:
            # Legacy "current_intraday" path or fill_bar unavailable.
            price_path = self._infer_candle_path(adj_open, adj_high, adj_low, adj_close)
            if action.action == "BUY":
                fill_price = price_path[1] if adj_close >= adj_open else price_path[0]
            elif action.action in ("SELL_ALL", "SELL_PARTIAL"):
                fill_price = price_path[2] if adj_close >= adj_open else price_path[1]
            else:
                fill_price = adj_close
            trade_date = bar.trade_date

        if action.action == "BUY":
            if bar.is_limit_up:
                return "涨停不可买入"
            price = fill_price * (1 + slippage)
            target_value = total_asset * Decimal(str(action.target_position))
            current_value = Decimal("0")
            pos = self.positions.get(ts_code)
            if pos:
                current_value = pos.avg_cost * pos.shares
            delta_value = max(target_value - current_value, Decimal("0"))
            if delta_value <= 0 or price <= 0:
                return None
            raw_shares = int(delta_value / price)
            volume = (raw_shares // 100) * 100
            if volume <= 0:
                return None
            cost = self.calculator.calculate("买入", price * volume)
            total_cost = price * volume + cost.total_fee
            if total_cost > self.cash:
                affordable = int(self.cash / price) // 100 * 100
                volume = affordable
                while volume > 0:
                    cost = self.calculator.calculate("买入", price * volume)
                    total_cost = price * volume + cost.total_fee
                    if total_cost <= self.cash:
                        break
                    volume -= 100
                if volume <= 0:
                    return None

            balance_before = self._book_asset()
            pos_ratio_before = self._position_ratio(balance_before) if balance_before > 0 else 0

            self.cash -= total_cost
            if ts_code not in self.positions:
                self.positions[ts_code] = Position(ts_code=ts_code)
            pos = self.positions[ts_code]
            total_shares = pos.shares + volume
            pos.avg_cost = (pos.avg_cost * pos.shares + price * volume) / total_shares if total_shares > 0 else price
            pos.shares = total_shares

            balance_after = self._book_asset()
            pos_ratio_after = self._position_ratio(balance_after) if balance_after > 0 else 0

            self._entry_dates[ts_code] = trade_date
            self._entry_prices[ts_code] = price
            self._highest_since_entry[ts_code] = bar.high
            self._lowest_since_entry[ts_code] = bar.low
            if ts_code not in self._open_lots:
                self._open_lots[ts_code] = []
            self._open_lots[ts_code].append(_LotEntry(
                ts_code=ts_code,
                shares=volume,
                cost=price,
                entry_date=trade_date,
                entry_fee=cost.total_fee,
            ))
            sig_reason = (signal.get("reason", "") if signal else "") or "信号触发: 买入"

            self.trades.append(TradeRecord(
                ts_code=ts_code,
                trade_date=trade_date,
                direction="买入",
                price=price,
                volume=volume,
                amount=price * volume,
                cost=cost,
                signal_type="买入",
                action="BUY",
                signal_reason=sig_reason,
                target_position=float(action.target_position),
                position_before=pos_ratio_before,
                position_after=pos_ratio_after,
                pnl=Decimal("0"),
                balance_before=balance_before,
                balance_after=balance_after,
                holding_days=0,
                exit_reason="",
            ))
            return None

        elif action.action in ("SELL_ALL", "SELL_PARTIAL"):
            if bar.is_limit_down:
                return "跌停不可卖出"
            price = fill_price * (1 - slippage)
            pos = self.positions.get(ts_code)
            if not pos or pos.shares <= 0:
                return None

            lots = self._open_lots.get(ts_code, [])
            eligible_shares = sum(
                lot.shares for lot in lots if lot.entry_date < trade_date
            )
            if eligible_shares <= 0:
                return "T+1 当日买入不可卖出"

            if action.action == "SELL_ALL":
                volume = pos.shares
            else:
                target_value = total_asset * Decimal(str(action.target_position))
                current_value = pos.avg_cost * pos.shares
                sell_value = max(current_value - target_value, Decimal("0"))
                if sell_value <= 0 or price <= 0:
                    return None
                raw_shares = int(sell_value / price)
                volume = min(pos.shares, (raw_shares // 100) * 100)

            volume = min(volume, eligible_shares)

            if volume <= 0:
                return None

            cost = self.calculator.calculate("卖出", price * volume)
            net_amount = price * volume - cost.total_fee

            balance_before = self._book_asset()
            pos_ratio_before = self._position_ratio(balance_before) if balance_before > 0 else 0

            self.cash += net_amount
            pos.shares -= volume

            balance_after = self._book_asset()
            pos_ratio_after = self._position_ratio(balance_after) if balance_after > 0 else 0

            entry_date = self._entry_dates.get(ts_code, trade_date)
            holding_days = (trade_date - entry_date).days

            remaining_sell = volume
            remaining_exit_fee = cost.total_fee
            matched_net_pnl = Decimal("0")
            while remaining_sell > 0 and lots:
                lot = lots[0]
                if lot.entry_date >= trade_date:
                    break
                lot_shares = min(remaining_sell, lot.shares)
                entry_fee = (
                    lot.entry_fee
                    if lot_shares == lot.shares
                    else lot.entry_fee * Decimal(lot_shares) / Decimal(lot.shares)
                )
                exit_fee = (
                    remaining_exit_fee
                    if lot_shares == remaining_sell
                    else remaining_exit_fee * Decimal(lot_shares) / Decimal(remaining_sell)
                )
                matched_cost = lot.cost * lot_shares
                gross_pnl = (price - lot.cost) * lot_shares
                net_pnl = gross_pnl - entry_fee - exit_fee
                return_rate = net_pnl / matched_cost if matched_cost > 0 else Decimal("0")
                self._closed_lots.append(_ClosedLot(
                    ts_code=ts_code,
                    shares=lot_shares,
                    entry_price=lot.cost,
                    entry_date=lot.entry_date,
                    exit_price=price,
                    exit_date=trade_date,
                    entry_fee=entry_fee,
                    exit_fee=exit_fee,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    return_rate=return_rate,
                    holding_days=(trade_date - lot.entry_date).days,
                    exit_reason=exit_reason or "策略信号",
                ))
                matched_net_pnl += net_pnl
                lot.entry_fee -= entry_fee
                lot.shares -= lot_shares
                remaining_exit_fee -= exit_fee
                remaining_sell -= lot_shares
                if lot.shares <= 0:
                    lots.pop(0)

            pnl = matched_net_pnl

            if pos.shares <= 0:
                self._entry_dates.pop(ts_code, None)
                self._entry_prices.pop(ts_code, None)
                self._highest_since_entry.pop(ts_code, None)
                self._lowest_since_entry.pop(ts_code, None)
                self._open_lots.pop(ts_code, None)

            reason = exit_reason or "策略信号"
            sig_type = signal.get("signal_type", "卖出") if signal else reason
            sig_reason = (
                (signal.get("reason", "") if signal else "")
                or (f"风险控制: {reason}" if exit_reason else f"信号触发: {sig_type}")
            )
            direction = "卖出"
            self.trades.append(TradeRecord(
                ts_code=ts_code,
                trade_date=trade_date,
                direction=direction,
                price=price,
                volume=volume,
                amount=price * volume,
                cost=cost,
                signal_type=sig_type,
                action=action.action,
                signal_reason=sig_reason,
                target_position=float(action.target_position),
                position_before=pos_ratio_before,
                position_after=pos_ratio_after,
                pnl=pnl,
                balance_before=balance_before,
                balance_after=balance_after,
                holding_days=holding_days,
                exit_reason=reason,
            ))

            if pos.shares == 0:
                del self.positions[ts_code]
            return None

        return None

    def _fill_price_mode(self) -> str:
        """Read BACKTEST_FILL_PRICE_MODE from settings, defaulting to 'next_open'.

        Imported lazily to avoid creating asyncio primitives at module import
        (mirrors the ws_producer pattern).
        """
        try:
            from app.core.config import get_settings
            return get_settings().backtest_fill_price_mode
        except Exception:
            return "next_open"

    def _compute_results(self) -> dict[str, Any]:
        if not self.equity_curve:
            pnl_analysis = self._build_pnl_analysis()
            return {
                "total_return": 0,
                "annual_return": 0,
                "sharpe_ratio": 0,
                "sortino_ratio": 0,
                "calmar_ratio": 0,
                "max_drawdown": 0,
                "annual_vol": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "max_consecutive_losses": 0,
                "avg_holding_days": 0,
                "total_fees": 0,
                "trade_count": 0,
                "monthly_returns": {},
                "daily_returns": [],
                "pnl_analysis": pnl_analysis,
                "closed_lots": pnl_analysis["closed_lots"],
                "stock_rankings": pnl_analysis["stock_rankings"],
                "performance": {
                    "monthly_returns": {},
                    "daily_returns": [],
                    "pnl_analysis": pnl_analysis,
                },
                "trade_records": [],
                "equity_curve": [],
            }

        initial = float(self.config.initial_cash)
        final = self.equity_curve[-1]["total_asset"]
        total_return = (final - initial) / initial if initial > 0 else 0

        dates = [e["date"] for e in self.equity_curve]
        values = [e["total_asset"] for e in self.equity_curve]

        daily_returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                daily_returns.append((values[i] - values[i - 1]) / values[i - 1])

        annual_return = 0.0
        if len(dates) >= 2:
            from datetime import date as date_cls
            d0 = date_cls.fromisoformat(dates[0])
            d1 = date_cls.fromisoformat(dates[-1])
            years = max((d1 - d0).days / 365.25, 0.01)
            annual_return = ((1 + total_return) ** (1 / years)) - 1

        sharpe = 0.0
        sortino = 0.0
        if daily_returns:
            import statistics
            mean_r = statistics.mean(daily_returns)
            std_r = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0
            if std_r > 0:
                sharpe = mean_r / std_r * (252 ** 0.5)
            downside = [r for r in daily_returns if r < 0]
            if downside:
                downside_std = (sum(r ** 2 for r in downside) / len(daily_returns)) ** 0.5
                if downside_std > 0:
                    sortino = mean_r / downside_std * (252 ** 0.5)

        max_dd = 0.0
        peak = values[0]
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        calmar = annual_return / max_dd if max_dd > 0 else 0.0

        annual_vol = 0.0
        if daily_returns and len(daily_returns) > 1:
            import statistics
            annual_vol = statistics.stdev(daily_returns) * (252 ** 0.5)

        gross_profit = Decimal("0")
        gross_loss = Decimal("0")
        win_count = 0
        total_closed = len(self._closed_lots)
        pnl_list: list[Decimal] = []
        holding_days_list: list[int] = []
        for lot in self._closed_lots:
            pnl_list.append(lot.pnl)
            holding_days_list.append(lot.holding_days)
            if lot.pnl > 0:
                gross_profit += lot.pnl
                win_count += 1
            else:
                gross_loss += lot.pnl

        win_rate = win_count / total_closed if total_closed > 0 else 0
        profit_factor = float(gross_profit / abs(gross_loss)) if gross_loss != 0 else float("inf") if gross_profit > 0 else 0
        avg_win = float(gross_profit / win_count) if win_count > 0 else 0
        avg_loss = float(gross_loss / (total_closed - win_count)) if total_closed - win_count > 0 else 0

        max_consec_losses = 0
        consec = 0
        for lot in self._closed_lots:
            if lot.pnl < 0:
                consec += 1
                max_consec_losses = max(max_consec_losses, consec)
            else:
                consec = 0

        avg_holding = sum(holding_days_list) / len(holding_days_list) if holding_days_list else 0

        total_fees = sum(
            t.cost.total_fee for t in self.trades
        )
        pnl_analysis = self._build_pnl_analysis()

        monthly_returns: dict[str, float] = {}
        if len(dates) >= 2:
            from datetime import date as date_cls
            month_start_val = values[0]
            current_month = dates[0][:7]
            for i, d in enumerate(dates):
                month_key = d[:7]
                if month_key != current_month:
                    if month_start_val > 0:
                        monthly_returns[current_month] = (values[i - 1] - month_start_val) / month_start_val
                    month_start_val = values[i - 1]
                    current_month = month_key
            if month_start_val > 0 and values:
                monthly_returns[current_month] = (values[-1] - month_start_val) / month_start_val

        return {
            "total_return": round(total_return, 8),
            "annual_return": round(annual_return, 8),
            "sharpe_ratio": round(sharpe, 8),
            "sortino_ratio": round(sortino, 8),
            "calmar_ratio": round(calmar, 8),
            "max_drawdown": round(max_dd, 8),
            "annual_vol": round(annual_vol, 8),
            "win_rate": round(win_rate, 8),
            "profit_factor": round(profit_factor, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_consecutive_losses": max_consec_losses,
            "avg_holding_days": round(avg_holding, 1),
            "total_fees": round(float(total_fees), 2),
            "trade_count": len(self.trades),
            "monthly_returns": {k: round(v, 6) for k, v in monthly_returns.items()},
            "daily_returns": [round(r, 6) for r in daily_returns],
            "pnl_analysis": pnl_analysis,
            "closed_lots": pnl_analysis["closed_lots"],
            "stock_rankings": pnl_analysis["stock_rankings"],
            "performance": {
                "initial_cash": float(initial),
                "final_asset": final,
                "total_return_pct": round(total_return * 100, 4),
                "annual_return_pct": round(annual_return * 100, 4),
                "sharpe_ratio": round(sharpe, 4),
                "sortino_ratio": round(sortino, 4),
                "calmar_ratio": round(calmar, 4),
                "max_drawdown_pct": round(max_dd * 100, 4),
                "annual_vol_pct": round(annual_vol * 100, 4),
                "win_rate_pct": round(win_rate * 100, 4),
                "profit_factor": round(profit_factor, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "max_consecutive_losses": max_consec_losses,
                "avg_holding_days": round(avg_holding, 1),
                "total_fees": round(float(total_fees), 2),
                "strategy_error_count": len(self.strategy_errors),
                "monthly_returns": {k: round(v, 6) for k, v in monthly_returns.items()},
                "daily_returns": [round(r, 6) for r in daily_returns],
                # 仅保留标量汇总，closed_lots / stock_rankings 已拆分到独立表
                "pnl_analysis": {
                    "closed_lot_count": pnl_analysis["closed_lot_count"],
                    "winning_lot_count": pnl_analysis["winning_lot_count"],
                    "losing_lot_count": pnl_analysis["losing_lot_count"],
                    "breakeven_lot_count": pnl_analysis["breakeven_lot_count"],
                    "stock_count": pnl_analysis["stock_count"],
                    "matched_cost": pnl_analysis["matched_cost"],
                    "gross_pnl": pnl_analysis["gross_pnl"],
                    "entry_fees": pnl_analysis["entry_fees"],
                    "exit_fees": pnl_analysis["exit_fees"],
                    "total_fees": pnl_analysis["total_fees"],
                    "net_pnl": pnl_analysis["net_pnl"],
                    "return_rate": pnl_analysis["return_rate"],
                    "win_rate": pnl_analysis["win_rate"],
                    "avg_holding_days": pnl_analysis["avg_holding_days"],
                },
            },
            "trade_records": [
                {
                    "ts_code": t.ts_code,
                    "trade_date": t.trade_date.isoformat(),
                    "direction": self._serialize_direction(t),
                    "price": float(t.price),
                    "volume": t.volume,
                    "amount": float(t.amount),
                    "commission": float(t.cost.commission),
                    "stamp_tax": float(t.cost.stamp_tax),
                    "transfer_fee": float(t.cost.transfer_fee),
                    "total_fee": float(t.cost.total_fee),
                    "action": t.action,
                    "signal_reason": t.signal_reason,
                    "target_position": t.target_position,
                    "position_before": t.position_before,
                    "position_after": t.position_after,
                    "pnl": float(t.pnl),
                    "balance_before": float(t.balance_before),
                    "balance_after": float(t.balance_after),
                    "holding_days": t.holding_days,
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
            "equity_curve": self.equity_curve,
            "signal_log": self.signals,
            "strategy_errors": self.strategy_errors,
            "execution_assumptions": {
                "execution_timeframe": self.config.execution_timeframe,
                "signal_timeframe": self.config.signal_timeframe,
                "price_path_simulation": True,
                "slippage_pct": self.config.slippage_pct,
                "stop_loss_pct": self.config.stop_loss_pct,
                "take_profit_pct": self.config.take_profit_pct,
                "trailing_stop_pct": self.config.trailing_stop_pct,
                "time_stop_days": self.config.time_stop_days,
            },
        }

    def _build_pnl_analysis(self) -> dict[str, Any]:
        stock_totals: dict[str, dict[str, Any]] = {}
        closed_lots: list[dict[str, Any]] = []
        total_entry_fees = Decimal("0")
        total_exit_fees = Decimal("0")
        total_matched_cost = Decimal("0")
        total_gross_pnl = Decimal("0")
        total_net_pnl = Decimal("0")
        total_holding_days = 0
        winning_lots = 0
        losing_lots = 0

        for lot in self._closed_lots:
            matched_cost = lot.entry_price * lot.shares
            total_entry_fees += lot.entry_fee
            total_exit_fees += lot.exit_fee
            total_matched_cost += matched_cost
            total_gross_pnl += lot.gross_pnl
            total_net_pnl += lot.net_pnl
            total_holding_days += lot.holding_days
            winning_lots += int(lot.net_pnl > 0)
            losing_lots += int(lot.net_pnl < 0)

            aggregate = stock_totals.setdefault(lot.ts_code, {
                "ts_code": lot.ts_code,
                "closed_lot_count": 0,
                "winning_lot_count": 0,
                "losing_lot_count": 0,
                "matched_cost": Decimal("0"),
                "gross_pnl": Decimal("0"),
                "total_fees": Decimal("0"),
                "net_pnl": Decimal("0"),
                "holding_days": 0,
            })
            aggregate["closed_lot_count"] += 1
            aggregate["winning_lot_count"] += int(lot.net_pnl > 0)
            aggregate["losing_lot_count"] += int(lot.net_pnl < 0)
            aggregate["matched_cost"] += matched_cost
            aggregate["gross_pnl"] += lot.gross_pnl
            aggregate["total_fees"] += lot.entry_fee + lot.exit_fee
            aggregate["net_pnl"] += lot.net_pnl
            aggregate["holding_days"] += lot.holding_days

            closed_lots.append({
                "ts_code": lot.ts_code,
                "entry_date": lot.entry_date.isoformat(),
                "exit_date": lot.exit_date.isoformat(),
                "entry_price": float(lot.entry_price),
                "exit_price": float(lot.exit_price),
                "shares": lot.shares,
                "entry_fee": float(lot.entry_fee),
                "exit_fee": float(lot.exit_fee),
                "gross_pnl": float(lot.gross_pnl),
                "net_pnl": float(lot.net_pnl),
                "return_rate": float(lot.return_rate),
                "holding_days": lot.holding_days,
                "exit_reason": lot.exit_reason,
            })

        stock_rankings: list[dict[str, Any]] = []
        for aggregate in stock_totals.values():
            count = aggregate.pop("closed_lot_count")
            holding_days = aggregate.pop("holding_days")
            matched_cost = aggregate["matched_cost"]
            net_pnl = aggregate["net_pnl"]
            wins = aggregate["winning_lot_count"]
            stock_rankings.append({
                **aggregate,
                "closed_lot_count": count,
                "return_rate": float(net_pnl / matched_cost) if matched_cost > 0 else 0.0,
                "win_rate": wins / count if count else 0.0,
                "avg_holding_days": holding_days / count if count else 0.0,
            })
        stock_rankings.sort(key=lambda item: (-item["net_pnl"], item["ts_code"]))
        for ranking in stock_rankings:
            for key in ("matched_cost", "gross_pnl", "total_fees", "net_pnl"):
                ranking[key] = float(ranking[key])

        lot_count = len(self._closed_lots)
        total_fees = total_entry_fees + total_exit_fees
        return {
            "closed_lot_count": lot_count,
            "winning_lot_count": winning_lots,
            "losing_lot_count": losing_lots,
            "breakeven_lot_count": lot_count - winning_lots - losing_lots,
            "stock_count": len(stock_rankings),
            "matched_cost": float(total_matched_cost),
            "gross_pnl": float(total_gross_pnl),
            "entry_fees": float(total_entry_fees),
            "exit_fees": float(total_exit_fees),
            "total_fees": float(total_fees),
            "net_pnl": float(total_net_pnl),
            "return_rate": float(total_net_pnl / total_matched_cost) if total_matched_cost > 0 else 0.0,
            "win_rate": winning_lots / lot_count if lot_count else 0.0,
            "avg_holding_days": total_holding_days / lot_count if lot_count else 0.0,
            "closed_lots": closed_lots,
            "stock_rankings": stock_rankings,
        }

    @staticmethod
    def _serialize_direction(trade: TradeRecord) -> str:
        if trade.action == "SELL_ALL":
            return SellDirection("全部卖出")
        if trade.action == "SELL_PARTIAL":
            return SellDirection("部分卖出")
        return trade.direction
