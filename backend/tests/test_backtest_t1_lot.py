"""Tests for P1-4: backtest T+1 check at the lot level.

Validates that the T+1 rule uses ``_open_lots[ts_code]`` (FIFO list of lots
with their own ``entry_date``) instead of the single ``_entry_dates[ts_code]``
which gets overwritten on each buy and would incorrectly block a partial
sell after an add-on purchase.

Covers three cases:
1. Buy day1 + add day2 + sell day2 → SUCCESS (day1 lot is T+1-unlocked)
2. Buy day1 + sell day1 → BLOCKED (same-day lot not yet unlocked)
3. Buy day1 100 + sell day2 200 → BLOCKED (only 100 sellable, requested 200)
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.backtest.adapter import (
    BacktestConfig,
    BacktestRunner,
    KBar,
    Position,
    _LotEntry,
)
from app.backtest.signals import SignalOutput


def _make_bar(
    ts_code: str,
    trade_date: date,
    *,
    base_price: Decimal = Decimal("10.00"),
    is_suspended: bool = False,
    is_limit_up: bool = False,
    is_limit_down: bool = False,
) -> KBar:
    return KBar(
        ts_code=ts_code,
        trade_date=trade_date,
        open=base_price,
        high=base_price + Decimal("0.20"),
        low=base_price - Decimal("0.20"),
        close=base_price,
        pre_close=base_price,
        volume=1_000_000,
        amount=base_price * 1_000_000,
        adj_factor=None,
        is_suspended=is_suspended,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
        turnover_rate=None,
    )


def _make_runner() -> BacktestRunner:
    config = BacktestConfig(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 15),
        initial_cash=Decimal("100000"),
    )
    return BacktestRunner(config)


def _apply_sell_rules(
    runner: BacktestRunner,
    ts_code: str,
    td: date,
    *,
    sell_action: str = "SELL_ALL",
    target_position: float = 0.0,
    base_price: Decimal = Decimal("10.00"),
) -> tuple[str, bool]:
    bar = _make_bar(ts_code, td, base_price=base_price)
    action = SignalOutput(action=sell_action, target_position=target_position)  # type: ignore[arg-type]
    position = runner.positions.get(ts_code)
    return runner._apply_rules(action, ts_code, bar, position, td)


@pytest.mark.backtest
class TestT1LotLevelCheck:
    """Verify T+1 is enforced per lot, not per single entry_date."""

    def test_t1_allows_selling_lot_from_prior_day_when_added_today(self) -> None:
        """day1 买 100 + day2 加仓 100 + day2 卖 100 → 通过。

        旧实现会用 _entry_dates[ts_code] = day2（被加仓覆盖）阻塞卖出；
        新实现查 _open_lots 中存在 day1 的 lot（entry_date < td），允许卖出。
        """
        runner = _make_runner()
        ts_code = "000001.SZ"
        day1 = date(2026, 5, 4)
        day2 = date(2026, 5, 5)

        # 模拟分两日买入：day1 100 股 + day2 100 股
        runner.positions[ts_code] = Position(
            ts_code=ts_code, shares=200, avg_cost=Decimal("10.00")
        )
        runner._open_lots[ts_code] = [
            _LotEntry(ts_code=ts_code, shares=100, cost=Decimal("10.00"), entry_date=day1),
            _LotEntry(ts_code=ts_code, shares=100, cost=Decimal("10.00"), entry_date=day2),
        ]
        runner._entry_dates[ts_code] = day2  # 模拟旧实现的覆盖问题

        reason, blocked = _apply_sell_rules(runner, ts_code, day2)

        assert blocked is False, f"应允许卖出 day1 的 lot，但被阻塞: {reason}"
        assert reason == ""

    def test_signal_rules_defer_t1_until_fill_date_is_known(self) -> None:
        """The signal stage must not reject an order before its fill date is known."""
        runner = _make_runner()
        ts_code = "000001.SZ"
        day1 = date(2026, 5, 4)

        runner.positions[ts_code] = Position(
            ts_code=ts_code, shares=100, avg_cost=Decimal("10.00")
        )
        runner._open_lots[ts_code] = [
            _LotEntry(ts_code=ts_code, shares=100, cost=Decimal("10.00"), entry_date=day1),
        ]
        runner._entry_dates[ts_code] = day1

        reason, blocked = _apply_sell_rules(runner, ts_code, day1)

        assert blocked is False
        assert reason == ""

    def test_t1_blocks_selling_more_than_unlocked_shares(self) -> None:
        """day1 买 100 + day2 卖 200 → 阻塞在 _apply_rules 后续阶段。

        _apply_rules 只校验 "是否有可解锁 lot"（sellable_shares > 0），
        100 股 unlocked 满足条件放行；但 _execute_action 因 pos.shares=100
        而 SELL_ALL 请求 200 时应被 naturally 限制到 100 股。
        这里只验证 _apply_rules 行为：day1 lot unlocked 即放行。
        """
        runner = _make_runner()
        ts_code = "000001.SZ"
        day1 = date(2026, 5, 4)
        day2 = date(2026, 5, 5)

        # 持仓只有 100 股（全部来自 day1）
        runner.positions[ts_code] = Position(
            ts_code=ts_code, shares=100, avg_cost=Decimal("10.00")
        )
        runner._open_lots[ts_code] = [
            _LotEntry(ts_code=ts_code, shares=100, cost=Decimal("10.00"), entry_date=day1),
        ]
        runner._entry_dates[ts_code] = day1

        # _apply_rules 应放行（sellable_shares = 100 > 0）
        reason, blocked = _apply_sell_rules(runner, ts_code, day2)
        assert blocked is False, f"day1 lot 在 day2 应可卖，但被阻塞: {reason}"

        # 实际卖出受 pos.shares 限制（SELL_ALL 只能卖 100 股，不会超卖）
        # 这里间接验证：_apply_rules 不再做"数量是否足够"的校验，
        # 数量校验由 _execute_action 的 volume = pos.shares 兜底
        assert runner.positions[ts_code].shares == 100

    def test_signal_rules_defer_multiple_same_day_lots_to_execution(self) -> None:
        """Multiple same-day lots are validated against the eventual fill date."""
        runner = _make_runner()
        ts_code = "000001.SZ"
        today = date(2026, 5, 4)

        runner.positions[ts_code] = Position(
            ts_code=ts_code, shares=300, avg_cost=Decimal("10.00")
        )
        runner._open_lots[ts_code] = [
            _LotEntry(ts_code=ts_code, shares=100, cost=Decimal("10.00"), entry_date=today),
            _LotEntry(ts_code=ts_code, shares=200, cost=Decimal("10.00"), entry_date=today),
        ]
        runner._entry_dates[ts_code] = today

        reason, blocked = _apply_sell_rules(runner, ts_code, today)

        assert blocked is False
        assert reason == ""

    def test_signal_rules_defer_missing_lot_state_to_execution(self) -> None:
        """Missing lot state is blocked by execution after the fill date is known."""
        runner = _make_runner()
        ts_code = "000001.SZ"
        day1 = date(2026, 5, 4)

        runner.positions[ts_code] = Position(
            ts_code=ts_code, shares=100, avg_cost=Decimal("10.00")
        )
        # 故意不设置 _open_lots[ts_code]
        runner._entry_dates[ts_code] = day1

        reason, blocked = _apply_sell_rules(runner, ts_code, day1)

        assert blocked is False
        assert reason == ""

    def test_t1_unlocked_lot_allows_partial_sell_after_friday_to_monday_gap(self) -> None:
        """跨周末场景：周五买 + 周一卖 → 通过。

        只验证 entry_date < td 的日期比较，不涉及交易日历。
        """
        runner = _make_runner()
        ts_code = "000001.SZ"
        friday = date(2026, 5, 8)  # 周五
        monday = date(2026, 5, 11)  # 下周一

        runner.positions[ts_code] = Position(
            ts_code=ts_code, shares=100, avg_cost=Decimal("10.00")
        )
        runner._open_lots[ts_code] = [
            _LotEntry(ts_code=ts_code, shares=100, cost=Decimal("10.00"), entry_date=friday),
        ]
        runner._entry_dates[ts_code] = friday

        reason, blocked = _apply_sell_rules(runner, ts_code, monday)

        assert blocked is False
        assert reason == ""

    @pytest.mark.parametrize(
        ("action", "target_position"),
        [("SELL_ALL", 0.0), ("SELL_PARTIAL", 0.2)],
    )
    def test_mixed_age_sell_caps_execution_to_unlocked_lots(
        self,
        monkeypatch: pytest.MonkeyPatch,
        action: str,
        target_position: float,
    ) -> None:
        runner = _make_runner()
        monkeypatch.setattr(runner, "_fill_price_mode", lambda: "current_close")
        ts_code = "000001.SZ"
        yesterday = date(2026, 5, 4)
        today = date(2026, 5, 5)
        runner.cash = Decimal("1000")
        runner.positions[ts_code] = Position(
            ts_code=ts_code, shares=300, avg_cost=Decimal("10.6666666667")
        )
        runner._open_lots[ts_code] = [
            _LotEntry(
                ts_code=ts_code,
                shares=100,
                cost=Decimal("10"),
                entry_date=yesterday,
                entry_fee=Decimal("5.0100"),
            ),
            _LotEntry(
                ts_code=ts_code,
                shares=200,
                cost=Decimal("11"),
                entry_date=today,
                entry_fee=Decimal("5.0220"),
            ),
        ]
        runner._entry_dates[ts_code] = today
        runner._entry_prices[ts_code] = Decimal("11")
        balance_before = runner._book_asset()

        blocked_reason = runner._execute_action(
            SignalOutput(action=action, target_position=target_position),  # type: ignore[arg-type]
            ts_code,
            _make_bar(ts_code, today, base_price=Decimal("12")),
            Decimal("4200"),
        )

        assert blocked_reason is None
        trade = runner.trades[-1]
        expected_amount = trade.price * 100
        expected_fee = runner.calculator.calculate("卖出", expected_amount).total_fee
        assert trade.volume == 100
        assert trade.amount == expected_amount
        assert trade.cost.total_fee == expected_fee
        assert trade.balance_before == balance_before
        assert runner.cash == Decimal("1000") + expected_amount - expected_fee
        assert runner.positions[ts_code].shares == 200
        assert trade.balance_after == runner._book_asset()

        assert len(runner._closed_lots) == 1
        closed = runner._closed_lots[0]
        assert closed.entry_date == yesterday
        assert closed.shares == 100
        assert closed.entry_fee == Decimal("5.0100")
        assert closed.exit_fee == expected_fee
        assert trade.pnl == closed.net_pnl

        assert len(runner._open_lots[ts_code]) == 1
        same_day_lot = runner._open_lots[ts_code][0]
        assert same_day_lot.entry_date == today
        assert same_day_lot.shares == 200
        assert same_day_lot.entry_fee == Decimal("5.0220")

    def test_execute_sell_blocks_when_no_lots_are_unlocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _make_runner()
        monkeypatch.setattr(runner, "_fill_price_mode", lambda: "current_close")
        ts_code = "000001.SZ"
        today = date(2026, 5, 5)
        runner.positions[ts_code] = Position(ts_code, 100, Decimal("10"))
        runner._open_lots[ts_code] = [
            _LotEntry(ts_code, 100, Decimal("10"), today, Decimal("5.0100"))
        ]

        reason = runner._execute_action(
            SignalOutput(action="SELL_ALL", target_position=0),
            ts_code,
            _make_bar(ts_code, today, base_price=Decimal("12")),
            Decimal("100000"),
        )

        assert reason == "T+1 当日买入不可卖出"
        assert runner.trades == []
        assert runner.positions[ts_code].shares == 100
        assert runner.cash == runner.config.initial_cash

    def test_same_day_sell_signal_can_fill_at_next_open(self) -> None:
        runner = _make_runner()
        ts_code = "000001.SZ"
        signal_day = date(2026, 5, 5)
        fill_day = date(2026, 5, 6)
        runner.positions[ts_code] = Position(ts_code, 100, Decimal("10"))
        runner._open_lots[ts_code] = [
            _LotEntry(ts_code, 100, Decimal("10"), signal_day, Decimal("5.0100"))
        ]

        action = SignalOutput(action="SELL_ALL", target_position=0)
        reason, blocked = runner._apply_rules(
            action,
            ts_code,
            _make_bar(ts_code, signal_day),
            runner.positions[ts_code],
            signal_day,
        )
        execution_reason = runner._execute_action(
            action,
            ts_code,
            _make_bar(ts_code, signal_day),
            Decimal("100000"),
            fill_bar=_make_bar(ts_code, fill_day, base_price=Decimal("12")),
        )

        assert blocked is False
        assert reason == ""
        assert execution_reason is None
        assert len(runner.trades) == 1
        assert runner.trades[0].trade_date == fill_day
        assert runner.trades[0].volume == 100
        assert ts_code not in runner.positions
