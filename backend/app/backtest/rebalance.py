"""Weekly rebalance planner for ranked mode backtesting (v2).

Provides the WeeklyRebalancePlanner class that generates and executes
weekly portfolio rebalance decisions using signal-qualified candidates,
recent return rates, and an equal-weight buffer-zone approach.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from app.backtest.adapter import BacktestConfig, KBar, SignalOutput


@dataclass
class RankInfo:
    ts_code: str
    return_rate: Decimal
    rank: int
    score_date: date


@dataclass
class HoldingInfo:
    ts_code: str
    shares: int
    market_value: Decimal
    avg_cost: Decimal
    entry_date: date
    exit_reason: str | None
    rank: RankInfo | None


@dataclass
class CandidateInfo:
    ts_code: str
    signal_type: str
    first_signal_date: date
    latest_signal_date: date
    rank: RankInfo | None
    exited: bool = False


@dataclass
class TargetPosition:
    ts_code: str
    target_weight: Decimal
    desired_shares: int
    side: Literal['hold', 'buy', 'sell']


@dataclass
class PlannedOrder:
    ts_code: str
    side: Literal['buy', 'sell']
    reason: str
    planned_shares: int
    executed_shares: int = 0
    fill_price: Decimal | None = None
    fees: Decimal = Decimal('0')
    status: str = 'pending'
    blocked_reason: str = ''


@dataclass
class RebalanceDecision:
    decision_date: date
    information_date: date
    fill_date: date | None = None
    score_coverage: float = 0.0
    candidate_count: int = 0
    holding_count_before: int = 0
    target_count: int = 0
    max_positions: int = 0
    buffer_size: int = 0
    nav_before: Decimal = Decimal("0")
    cash_before: Decimal = Decimal("0")
    plans: list[PlannedOrder] = field(default_factory=list)
    holding_count_after: int = 0
    cash_after: Decimal = Decimal("0")
    turnover: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    status: str = 'planned'
    diagnostics: dict[str, Any] = field(default_factory=dict)


class WeeklyRebalancePlanner:
    """Portfolio rebalance planner - weekly, signal-qualified, equal-weight."""

    def __init__(self, config: BacktestConfig, runner: 'BacktestRunner'):
        self.config = config
        self.runner = runner
        self.candidate_pool: dict[str, CandidateInfo] = {}
        self.last_decision_date: date | None = None
        self.weekly_plan: RebalanceDecision | None = None

    def on_signal(self, ts_code: str, signal_type: str, td: date, has_position: bool) -> None:
        """Called when a buy/add signal is generated. Adds to candidate pool."""
        if signal_type in ('买入', '增持'):
            if ts_code in self.candidate_pool:
                existing = self.candidate_pool[ts_code]
                existing.latest_signal_date = td
                existing.exited = False
            else:
                self.candidate_pool[ts_code] = CandidateInfo(
                    ts_code=ts_code, signal_type=signal_type,
                    first_signal_date=td, latest_signal_date=td,
                    rank=None, exited=False,
                )

    def on_exit(self, ts_code: str, exit_reason: str) -> None:
        """Called when a risk exit or strategy sell signal triggers."""
        if ts_code in self.candidate_pool:
            self.candidate_pool[ts_code].exited = True

    def should_run_weekly(self, td: date, trading_dates: list[date]) -> bool:
        """Check if td is the last open day of the week using trade_calendar."""
        if not trading_dates or td not in trading_dates:
            return False
        idx = trading_dates.index(td)
        if idx + 1 >= len(trading_dates):
            return True
        next_td = trading_dates[idx + 1]
        return next_td.isocalendar()[1] != td.isocalendar()[1]

    def _find_return_rate(
        self,
        ts_code: str,
        td: date,
        all_klines: dict,
        lookback_window: int = 20,
    ) -> RankInfo | None:
        """Find recent return rate (N-day) for a stock on a given date.

        Uses the past N-day return computed from K-line data.
        Returns None if there's not enough data.
        """
        klines = all_klines.get(ts_code, [])
        if not klines:
            return None

        # Find the K-bar index for target date
        current_idx = None
        for i, k in enumerate(klines):
            if k.trade_date == td:
                current_idx = i
                break

        if current_idx is None or current_idx < lookback_window:
            return None

        past_k = klines[current_idx - lookback_window]
        curr_k = klines[current_idx]
        if past_k.close <= 0:
            return None

        return_rate = (curr_k.close - past_k.close) / past_k.close
        return RankInfo(
            ts_code=ts_code,
            return_rate=Decimal(str(return_rate)),
            rank=999999,
            score_date=td,
        )

    def plan(
        self,
        td: date,
        all_klines: dict,
        total_asset: Decimal,
        trading_dates: list[date],
    ) -> RebalanceDecision | None:
        """Generate the weekly rebalance plan. Returns None if no rebalance needed."""
        # 1. Gather current holdings from runner.positions
        holdings: dict[str, HoldingInfo] = {}
        for ts_code, pos in self.runner.positions.items():
            if pos.shares <= 0:
                continue
            klines = all_klines.get(ts_code, [])
            price = None
            for k in reversed(klines):
                if k.trade_date <= td and k.close:
                    price = k.close
                    break
            market_value = (price * pos.shares) if price else Decimal("0")
            entry_date = self.runner._entry_dates.get(ts_code, td)
            holdings[ts_code] = HoldingInfo(
                ts_code=ts_code, shares=pos.shares, market_value=market_value,
                avg_cost=pos.avg_cost, entry_date=entry_date,
                exit_reason=None, rank=None,
            )

        # 2. Merge candidate pool, remove exited ones
        active_candidates = {
            k: v for k, v in self.candidate_pool.items() if not v.exited
        }

        if not active_candidates and not holdings:
            return None

        # 3. Fetch return rates for all eligible stocks
        candidate_ranks: dict[str, RankInfo | None] = {}
        for ts_code in active_candidates:
            rank = self._find_return_rate(ts_code, td, all_klines)
            candidate_ranks[ts_code] = rank

        # Also rank current holdings that are not in the candidate pool
        for ts_code in holdings:
            if ts_code not in candidate_ranks:
                rank = self._find_return_rate(ts_code, td, all_klines)
                candidate_ranks[ts_code] = rank
                holdings[ts_code].rank = rank

        # 4. Rank by return_rate DESC, then ts_code
        union_set = set(active_candidates.keys()) | set(holdings.keys())
        ranked_items = []
        for ts_code in union_set:
            rank = candidate_ranks.get(ts_code)
            if rank is not None:
                ranked_items.append((ts_code, rank))
            else:
                ranked_items.append((ts_code, RankInfo(
                    ts_code=ts_code, return_rate=Decimal("0"), rank=999999,
                    score_date=td,
                )))

        ranked_items.sort(key=lambda x: (-x[1].return_rate, x[0]))

        # 5. Apply 20% buffer zone
        max_pos = self.config.max_positions
        if max_pos <= 0:
            max_pos = max(len(ranked_items), 1)
        buffer_size = max(1, int(max_pos * getattr(self.config, 'rank_buffer_pct', 0.2)))

        # Top N = core, next buffer = buffer zone
        core_set = set(item[0] for item in ranked_items[:max_pos])
        buffer_set = set(item[0] for item in ranked_items[max_pos:max_pos + buffer_size])

        # Current holdings in core or buffer zone -> keep
        keep_set = set()
        for ts_code in holdings:
            if ts_code in core_set or ts_code in buffer_set:
                keep_set.add(ts_code)

        # Final target = core + keep from buffer (buffer holdings stay even if over max_pos)
        final_target = set(core_set)
        for ts_code in keep_set:
            if ts_code not in final_target:
                final_target.add(ts_code)

        # 6. Build equal-weight target list
        target_count = len(final_target)
        if target_count == 0:
            return None
        equal_weight = Decimal("1.0") / Decimal(str(target_count))

        # 7. Compute target shares considering 100-share lots
        target_positions: dict[str, TargetPosition] = {}
        for ts_code in final_target:
            klines = all_klines.get(ts_code, [])
            price = None
            for k in reversed(klines):
                if k.trade_date <= td and k.close:
                    price = k.close
                    break
            if not price or price <= 0:
                continue
            target_value = total_asset * equal_weight
            raw_shares = int(target_value / price)
            desired_shares = (raw_shares // 100) * 100
            if desired_shares <= 0:
                desired_shares = 100
            target_positions[ts_code] = TargetPosition(
                ts_code=ts_code, target_weight=equal_weight,
                desired_shares=desired_shares,
                side='hold' if ts_code in holdings else 'buy',
            )

        # 8. Create PlannedOrders for sells and buys
        plans: list[PlannedOrder] = []

        for ts_code in holdings:
            if ts_code not in final_target:
                pos = holdings[ts_code]
                plans.append(PlannedOrder(
                    ts_code=ts_code, side='sell', reason='调仓卖出: 不在目标组合中',
                    planned_shares=pos.shares,
                ))

        for ts_code, tp in target_positions.items():
            current_shares = holdings[ts_code].shares if ts_code in holdings else 0
            if tp.desired_shares > current_shares:
                buy_shares = tp.desired_shares - current_shares
                plans.append(PlannedOrder(
                    ts_code=ts_code, side='buy', reason='调仓买入: 目标权重',
                    planned_shares=buy_shares,
                ))

        # 9. Calculate return rate coverage
        score_coverage = 0.0
        if active_candidates:
            covered = sum(1 for s in candidate_ranks.values() if s is not None)
            score_coverage = covered / len(active_candidates)

        return RebalanceDecision(
            decision_date=td,
            information_date=td,
            score_coverage=score_coverage,
            candidate_count=len(active_candidates),
            holding_count_before=len(holdings),
            target_count=target_count,
            max_positions=max_pos,
            buffer_size=buffer_size,
            nav_before=total_asset,
            cash_before=self.runner.cash,
            plans=plans,
            holding_count_after=target_count,
            cash_after=self.runner.cash,
            turnover=Decimal("0"),
            fees=Decimal("0"),
            status='planned',
            diagnostics={},
        )

    def execute(self, plan: RebalanceDecision, fill_bar_map: dict[str, KBar]) -> RebalanceDecision:
        """Execute the planned orders at fill_date using provided fill bars."""
        sell_orders = [p for p in plan.plans if p.side == 'sell']
        buy_orders = [p for p in plan.plans if p.side == 'buy']

        total_fees = Decimal("0")
        total_turnover = Decimal("0")
        nav_before = self.runner._calc_total_asset(self.runner._all_klines, plan.fill_date or plan.information_date)

        # Execute sells first
        for order in sell_orders:
            bar = fill_bar_map.get(order.ts_code)
            if bar is None:
                order.status = 'blocked'
                order.blocked_reason = '停牌或无数据'
                continue
            if bar.is_limit_down:
                order.status = 'blocked'
                order.blocked_reason = '跌停不可卖出'
                continue

            pos = self.runner.positions.get(order.ts_code)
            if not pos or pos.shares <= 0:
                order.status = 'blocked'
                order.blocked_reason = '无持仓'
                continue

            total_asset = self.runner._calc_total_asset(self.runner._all_klines, bar.trade_date)
            action = SignalOutput(action="SELL_ALL", target_position=0.0)
            result = self.runner._execute_action(
                action, order.ts_code, bar, total_asset,
                signal={"signal_type": "调仓", "reason": order.reason},
                exit_reason="调仓",
                fill_bar=bar,
            )
            if result is None:
                order.status = 'filled'
                order.executed_shares = order.planned_shares
                order.fill_price = bar.open
                total_turnover += bar.open * order.planned_shares
                self.runner.signals.append({
                    "ts_code": order.ts_code,
                    "trade_date": bar.trade_date.isoformat(),
                    "signal_type": "调仓",
                    "action": "SELL_ALL",
                    "target_position": 0.0,
                    "exit_reason": "调仓",
                })
            else:
                order.status = 'blocked'
                order.blocked_reason = result

        # Execute buys second (cash freed up by sells)
        for order in buy_orders:
            bar = fill_bar_map.get(order.ts_code)
            if bar is None:
                order.status = 'blocked'
                order.blocked_reason = '停牌或无数据'
                continue
            if bar.is_limit_up:
                order.status = 'blocked'
                order.blocked_reason = '涨停不可买入'
                continue

            total_asset = self.runner._calc_total_asset(self.runner._all_klines, bar.trade_date)
            if total_asset <= 0:
                order.status = 'blocked'
                order.blocked_reason = '净值归零'
                continue

            price = bar.open
            target_value = price * order.planned_shares
            target_weight = float(target_value / total_asset) if total_asset > 0 else 0.0
            target_weight = min(target_weight, 1.0)

            action = SignalOutput(action="BUY", target_position=target_weight)
            result = self.runner._execute_action(
                action, order.ts_code, bar, total_asset,
                signal={"signal_type": "调仓", "reason": order.reason},
                fill_bar=bar,
            )
            if result is None:
                order.status = 'filled'
                order.executed_shares = order.planned_shares
                order.fill_price = bar.open
                total_turnover += price * order.planned_shares
            else:
                order.status = 'blocked'
                order.blocked_reason = result

        filled = sum(1 for p in plan.plans if p.status == 'filled')
        blocked = sum(1 for p in plan.plans if p.status == 'blocked')
        all_filled = filled == len(plan.plans) if plan.plans else False

        plan.status = 'executed' if all_filled else ('partial' if filled > 0 else 'failed')
        plan.cash_after = self.runner.cash
        plan.fees = total_fees
        plan.turnover = total_turnover
        plan.diagnostics['filled_count'] = filled
        plan.diagnostics['blocked_count'] = blocked
        plan.diagnostics['turnover_pct'] = float(total_turnover / nav_before * 100) if nav_before > 0 else 0.0

        self.candidate_pool.clear()
        self.last_decision_date = plan.fill_date

        return plan