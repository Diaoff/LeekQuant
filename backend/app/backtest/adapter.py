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

from app.backtest.cost import AShareCostCalculator, CostResult, FeeConfig
from app.backtest.signals import SignalInput, SignalOutput, apply_cn_rules, map_signal_to_action
from app.backtest.strategy_runtime import StrategyExecutionResult, execute_strategy


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

    execution_timeframe: str = "1D"
    signal_timeframe: str = "1D"
    factor_scores_by_date: dict[date, dict[str, dict[str, Any]]] = field(default_factory=dict)


@dataclass(slots=True)
class _SignalCandidate:
    ts_code: str
    bar: KBar
    action: SignalOutput
    signal: dict[str, Any] | None
    exit_reason: str | None = None
    buy_priority_score: Decimal = Decimal("0")
    buy_priority_source: str = "default"
    factor_score: Decimal | None = None
    factor_rank: int | None = None


class BacktestContext:
    """Context object exposed to user strategy code."""

    def __init__(self, klines: list[KBar], positions: dict[str, Position], total_asset: Decimal, current_price: Decimal | None = None):
        self._klines = klines
        self._current_position: float = 0.0
        self.current_price = float(current_price) if current_price is not None else None

    @property
    def close(self) -> list[float]:
        return [float(k.close) for k in self._klines]

    @property
    def open(self) -> list[float]:
        return [float(k.open) for k in self._klines]

    @property
    def high(self) -> list[float]:
        return [float(k.high) for k in self._klines]

    @property
    def low(self) -> list[float]:
        return [float(k.low) for k in self._klines]

    @property
    def volume(self) -> list[int]:
        return [k.volume for k in self._klines]

    @property
    def amount(self) -> list[float]:
        return [float(k.amount) for k in self._klines]

    @property
    def trade_date(self) -> date:
        return self._klines[-1].trade_date if self._klines else date.today()

    @property
    def current_position(self) -> float:
        return self._current_position

    @current_position.setter
    def current_position(self, value: float) -> None:
        self._current_position = value


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

    @staticmethod
    def _infer_candle_path(open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> list[Decimal]:
        if close >= open_:
            return [open_, low, high, close]
        else:
            return [open_, high, low, close]

    def _check_exit_conditions(self, ts_code: str, price: Decimal, bar: KBar) -> str | None:
        entry_price = self._entry_prices.get(ts_code)
        if entry_price is None or entry_price <= 0:
            return None
        if self.config.stop_loss_pct > 0:
            loss_pct = float((entry_price - price) / entry_price)
            if loss_pct >= self.config.stop_loss_pct:
                return "止损"
        if self.config.take_profit_pct > 0:
            profit_pct = float((price - entry_price) / entry_price)
            if profit_pct >= self.config.take_profit_pct:
                return "止盈"
        if self.config.trailing_stop_pct > 0 and self.config.trailing_activation_pct > 0:
            highest = self._highest_since_entry.get(ts_code, entry_price)
            activated = float((highest - entry_price) / entry_price) >= self.config.trailing_activation_pct
            if activated:
                trail_pct = float((highest - price) / highest)
                if trail_pct >= self.config.trailing_stop_pct:
                    return "移动止盈"
        if self.config.time_stop_days > 0:
            entry_date = self._entry_dates.get(ts_code)
            if entry_date is not None and (bar.trade_date - entry_date).days >= self.config.time_stop_days:
                pnl_pct = float((price - entry_price) / entry_price)
                if pnl_pct <= 0:
                    return "时间止损"
        return None

    def run(self, all_klines: dict[str, list[KBar]]) -> dict[str, Any]:
        """Run backtest for all stocks in the pool."""
        trading_dates = self._get_trading_dates(all_klines)
        if not trading_dates:
            raise ValueError("no trading dates found in the specified range")

        lookback = 60
        for td in trading_dates:
            total_asset = self._calc_total_asset(all_klines, td)
            self.equity_curve.append({
                "date": td.isoformat(),
                "total_asset": float(total_asset),
                "cash": float(self.cash),
            })

            candidates: list[_SignalCandidate] = []
            for ts_code in self.config.stock_pool:
                klines = all_klines.get(ts_code, [])
                window = [k for k in klines if k.trade_date <= td][-lookback:]
                if not window or window[-1].trade_date != td:
                    continue

                bar = window[-1]
                if bar.is_suspended:
                    continue

                price_path = self._infer_candle_path(bar.open, bar.high, bar.low, bar.close)

                if ts_code in self.positions and self.positions[ts_code].shares > 0:
                    cur_high = self._highest_since_entry.get(ts_code, bar.high)
                    cur_low = self._lowest_since_entry.get(ts_code, bar.low)
                    self._highest_since_entry[ts_code] = max(cur_high, bar.high)
                    self._lowest_since_entry[ts_code] = min(cur_low, bar.low)

                exit_reason = self._check_exit_conditions(ts_code, price_path[0], bar)
                if exit_reason:
                    exec_action = SignalOutput(action="SELL_ALL", target_position=0.0)
                    candidates.append(_SignalCandidate(
                        ts_code=ts_code,
                        bar=bar,
                        action=exec_action,
                        signal=None,
                        exit_reason=exit_reason,
                    ))
                    continue

                ctx = BacktestContext(window, self.positions, total_asset)
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

                candidates.append(self._build_signal_candidate(ts_code, bar, action_info, signal, td))

            for candidate in sorted(candidates, key=self._candidate_sort_key):
                match_blocked_reason = self._execute_action(
                    candidate.action,
                    candidate.ts_code,
                    candidate.bar,
                    total_asset,
                    candidate.signal,
                    exit_reason=candidate.exit_reason,
                )
                signal_record = self._candidate_signal_record(candidate)
                if match_blocked_reason:
                    signal_record["match_status"] = "BLOCKED"
                    signal_record["reason"] = match_blocked_reason
                self.signals.append(signal_record)

        return self._compute_results()

    def _build_signal_candidate(
        self,
        ts_code: str,
        bar: KBar,
        action: SignalOutput,
        signal: dict[str, Any],
        td: date,
    ) -> _SignalCandidate:
        priority_score = Decimal("0")
        priority_source = "default"
        factor_score: Decimal | None = None
        factor_rank: int | None = None

        if signal.get("signal_type") in ("买入", "增持"):
            factor = self.config.factor_scores_by_date.get(td, {}).get(ts_code)
            if factor is not None:
                factor_score = self._decimal_or_none(factor.get("total_score"))
                if factor.get("rank") is not None:
                    factor_rank = int(factor["rank"])

            confidence = self._decimal_or_none(signal.get("confidence"))
            if confidence is not None:
                priority_score = confidence
                priority_source = "confidence"
            elif factor_score is not None:
                priority_score = factor_score
                priority_source = "factor_score"

        return _SignalCandidate(
            ts_code=ts_code,
            bar=bar,
            action=action,
            signal=signal,
            buy_priority_score=priority_score,
            buy_priority_source=priority_source,
            factor_score=factor_score,
            factor_rank=factor_rank,
        )

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))

    @staticmethod
    def _candidate_sort_key(candidate: _SignalCandidate) -> tuple[int, int, Decimal, Decimal, str]:
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
            source_order = {"confidence": 0, "factor_score": 1}.get(candidate.buy_priority_source, 2)
        target_position = Decimal(str(candidate.action.target_position or 0))
        return group, source_order, -candidate.buy_priority_score, -target_position, candidate.ts_code

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
                "factor_score": str(candidate.factor_score) if candidate.factor_score is not None else None,
                "factor_rank": candidate.factor_rank,
            })
        return record

    def _get_trading_dates(self, all_klines: dict[str, list[KBar]]) -> list[date]:
        dates: set[date] = set()
        for klines in all_klines.values():
            for k in klines:
                if self.config.start_date <= k.trade_date <= self.config.end_date:
                    dates.add(k.trade_date)
        return sorted(dates)

    def _calc_total_asset(self, all_klines: dict[str, list[KBar]], td: date) -> Decimal:
        position_value = Decimal("0")
        for ts_code, pos in self.positions.items():
            if pos.shares <= 0:
                continue
            klines = all_klines.get(ts_code, [])
            price = None
            for k in reversed(klines):
                if k.trade_date <= td and k.close:
                    price = k.close
                    break
            if price:
                position_value += price * pos.shares
        return self.cash + position_value

    def _exec_strategy(self, ctx: BacktestContext, total_asset: Decimal) -> StrategyExecutionResult:
        try:
            ctx.current_position = self._position_ratio(total_asset)
        except AttributeError:
            pass
        return execute_strategy(self.config.source_code, ctx)

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
        return "", False

    def _execute_action(
        self,
        action: SignalOutput,
        ts_code: str,
        bar: KBar,
        total_asset: Decimal,
        signal: dict | None = None,
        exit_reason: str | None = None,
    ) -> str | None:
        price_path = self._infer_candle_path(bar.open, bar.high, bar.low, bar.close)

        # 选择买入时更优的价格（阳线取low，阴线取open）
        buy_price = price_path[1] if bar.close >= bar.open else price_path[0]
        sell_price = price_path[2] if bar.close >= bar.open else price_path[1]

        if action.action == "BUY":
            if bar.is_limit_up:
                return "涨停不可买入"
            price = buy_price
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

            self._entry_dates[ts_code] = bar.trade_date
            self._entry_prices[ts_code] = price
            self._highest_since_entry[ts_code] = bar.high
            self._lowest_since_entry[ts_code] = bar.low
            sig_reason = (signal.get("reason", "") if signal else "") or "信号触发: 买入"

            self.trades.append(TradeRecord(
                ts_code=ts_code,
                trade_date=bar.trade_date,
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
            price = sell_price
            pos = self.positions.get(ts_code)
            if not pos or pos.shares <= 0:
                return None

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

            if volume <= 0:
                return None

            cost = self.calculator.calculate("卖出", price * volume)
            net_amount = price * volume - cost.total_fee

            balance_before = self._book_asset()
            pos_ratio_before = self._position_ratio(balance_before) if balance_before > 0 else 0

            self.cash += net_amount
            pos.shares -= volume

            pnl = (price - pos.avg_cost) * volume - cost.total_fee

            balance_after = self._book_asset()
            pos_ratio_after = self._position_ratio(balance_after) if balance_after > 0 else 0

            entry_date = self._entry_dates.get(ts_code, bar.trade_date)
            holding_days = (bar.trade_date - entry_date).days

            if pos.shares <= 0:
                self._entry_dates.pop(ts_code, None)
                self._entry_prices.pop(ts_code, None)
                self._highest_since_entry.pop(ts_code, None)
                self._lowest_since_entry.pop(ts_code, None)

            reason = exit_reason or "策略信号"
            sig_type = signal.get("signal_type", "卖出") if signal else reason
            sig_reason = (
                (signal.get("reason", "") if signal else "")
                or (f"风险控制: {reason}" if exit_reason else f"信号触发: {sig_type}")
            )
            direction = "卖出"
            self.trades.append(TradeRecord(
                ts_code=ts_code,
                trade_date=bar.trade_date,
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

    def _compute_results(self) -> dict[str, Any]:
        if not self.equity_curve:
            return {
                "total_return": 0,
                "annual_return": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0,
                "annual_vol": 0,
                "win_rate": 0,
                "trade_count": 0,
                "performance": {},
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
        if daily_returns:
            import statistics
            mean_r = statistics.mean(daily_returns)
            std_r = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0
            if std_r > 0:
                sharpe = mean_r / std_r * (252 ** 0.5)

        max_dd = 0.0
        peak = values[0]
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        annual_vol = 0.0
        if daily_returns and len(daily_returns) > 1:
            import statistics
            annual_vol = statistics.stdev(daily_returns) * (252 ** 0.5)

        buy_trades = [t for t in self.trades if t.direction == "买入"]
        sell_trades = [t for t in self.trades if t.direction == "卖出"]
        win_count = 0
        total_rounds = 0
        for buy in buy_trades:
            matching_sells = [
                s for s in sell_trades
                if s.ts_code == buy.ts_code and s.trade_date >= buy.trade_date
            ]
            if matching_sells:
                total_rounds += 1
                sell_price = matching_sells[0].price
                if sell_price > buy.price:
                    win_count += 1

        win_rate = win_count / total_rounds if total_rounds > 0 else 0

        return {
            "total_return": round(total_return, 8),
            "annual_return": round(annual_return, 8),
            "sharpe_ratio": round(sharpe, 8),
            "max_drawdown": round(max_dd, 8),
            "annual_vol": round(annual_vol, 8),
            "win_rate": round(win_rate, 8),
            "trade_count": len(self.trades),
            "performance": {
                "initial_cash": float(initial),
                "final_asset": final,
                "total_return_pct": round(total_return * 100, 4),
                "annual_return_pct": round(annual_return * 100, 4),
                "sharpe_ratio": round(sharpe, 4),
                "max_drawdown_pct": round(max_dd * 100, 4),
                "annual_vol_pct": round(annual_vol * 100, 4),
                "win_rate_pct": round(win_rate * 100, 4),
                "strategy_error_count": len(self.strategy_errors),
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
                "stop_loss_pct": self.config.stop_loss_pct,
                "take_profit_pct": self.config.take_profit_pct,
                "trailing_stop_pct": self.config.trailing_stop_pct,
                "time_stop_days": self.config.time_stop_days,
            },
        }

    @staticmethod
    def _serialize_direction(trade: TradeRecord) -> str:
        if trade.action == "SELL_ALL":
            return SellDirection("全部卖出")
        if trade.action == "SELL_PARTIAL":
            return SellDirection("部分卖出")
        return trade.direction
