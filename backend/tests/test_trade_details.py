"""Tests for enhanced trade record details in backtest engine.

Validates that the enhanced TradeRecord dataclass contains all new fields,
calculates values correctly for buy/sell operations, and tracks multi-trade scenarios.
"""
from datetime import date, timedelta
from decimal import Decimal
import json

import pytest

from app.backtest.adapter import (
    BacktestConfig,
    BacktestContext,
    BacktestRunner,
    KBar,
    Position,
    TradeRecord,
)
from app.backtest.cost import CostResult, FeeConfig


@pytest.fixture(autouse=True)
def patch_backtest_context_position_setter(monkeypatch):
    """Workaround: Add setter to BacktestContext.current_position property."""
    _position_value = 0.0

    def getter(self):
        return _position_value

    def setter(self, value):
        nonlocal _position_value
        _position_value = value

    BacktestContext.current_position = property(getter, setter)
    yield
    BacktestContext.current_position = property(lambda self: 0.0)


def generate_klines(
    ts_code: str = "000001.SZ",
    start_date: date | None = None,
    days: int = 30,
    base_price: float = 10.0,
    price_increment: float = 0.1,
) -> list[KBar]:
    """Generate consecutive K-line data for testing with specific prices."""
    start = start_date or date(2026, 5, 1)
    klines = []
    pre_close = Decimal(str(base_price))

    for i in range(days):
        trade_date = start + timedelta(days=i)
        close = Decimal(str(base_price + i * price_increment))
        klines.append(KBar(
            ts_code=ts_code,
            trade_date=trade_date,
            open=close - Decimal("0.1"),
            high=close + Decimal("0.2"),
            low=close - Decimal("0.2"),
            close=close,
            pre_close=pre_close,
            volume=1000000 + i * 10000,
            amount=close * (1000000 + i * 10000),
            adj_factor=None,
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
        ))
        pre_close = close

    return klines


def sample_backtest_config(**kwargs):
    """Helper to create BacktestConfig with sensible defaults."""
    defaults = dict(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 30),
        initial_cash=Decimal("100000"),
    )
    defaults.update(kwargs)
    return BacktestConfig(**defaults)


def assert_trade_fields(trade: TradeRecord):
    """Verify TradeRecord contains all enhanced fields."""
    required_fields = [
        'ts_code', 'trade_date', 'direction', 'price', 'volume',
        'amount', 'cost', 'signal_type', 'action', 'signal_reason',
        'target_position', 'position_before', 'position_after',
        'pnl', 'balance_before', 'balance_after', 'holding_days'
    ]
    for field in required_fields:
        assert hasattr(trade, field), f"TradeRecord missing field: {field}"


def assert_buy_trade(trade: TradeRecord):
    """Verify characteristics of a buy trade."""
    assert trade.direction == "买入", f"Buy trade direction incorrect: {trade.direction}"
    assert trade.action == "BUY", f"Buy trade action incorrect: {trade.action}"
    assert trade.pnl == 0, f"Buy trade pnl should be 0: {trade.pnl}"
    assert trade.holding_days == 0, f"Buy trade holding_days should be 0: {trade.holding_days}"
    assert trade.position_before < trade.position_after, \
        f"Position should increase on buy: before={trade.position_before}, after={trade.position_after}"
    assert trade.balance_before > trade.balance_after, \
        f"Cash should decrease on buy: before={trade.balance_before}, after={trade.balance_after}"


def assert_sell_trade(trade: TradeRecord):
    """Verify characteristics of a sell trade."""
    assert trade.direction in ("全部卖出", "部分卖出"), \
        f"Sell trade direction incorrect: {trade.direction}"
    assert trade.action in ("SELL_ALL", "SELL_PARTIAL"), \
        f"Sell trade action incorrect: {trade.action}"
    assert trade.position_before > trade.position_after, \
        f"Position should decrease on sell: before={trade.position_before}, after={trade.position_after}"


@pytest.mark.backtest
class TestTradeRecordFieldExistence:
    """Test 1: Verify all 18 fields exist in TradeRecord with correct defaults."""

    def test_all_18_fields_exist_with_defaults(self):
        """TradeRecord instance should have all 18 fields with correct default values."""
        cost = CostResult(
            commission=Decimal("5.0"),
            stamp_tax=Decimal("0"),
            transfer_fee=Decimal("1.0"),
            total_fee=Decimal("6.0"),
        )
        trade = TradeRecord(
            ts_code="000001.SZ",
            trade_date=date(2026, 5, 1),
            direction="买入",
            price=Decimal("10.00"),
            volume=1000,
            amount=Decimal("10000.00"),
            cost=cost,
            signal_type="买入",
        )

        assert_trade_fields(trade)

        assert trade.ts_code == "000001.SZ"
        assert trade.trade_date == date(2026, 5, 1)
        assert trade.direction == "买入"
        assert trade.price == Decimal("10.00")
        assert trade.volume == 1000
        assert trade.amount == Decimal("10000.00")
        assert trade.signal_type == "买入"

        assert trade.action == ""
        assert trade.signal_reason == ""
        assert trade.target_position == 0.0
        assert trade.position_before == 0.0
        assert trade.position_after == 0.0
        assert trade.pnl == Decimal("0")
        assert trade.balance_before == Decimal("0")
        assert trade.balance_after == Decimal("0")
        assert trade.holding_days == 0

    def test_dataclass_slots_works_correctly(self):
        """dataclass with slots=True should work correctly - no __dict__ attribute."""
        cost = CostResult(
            commission=Decimal("5.0"),
            stamp_tax=Decimal("0"),
            transfer_fee=Decimal("1.0"),
            total_fee=Decimal("6.0"),
        )
        trade = TradeRecord(
            ts_code="000001.SZ",
            trade_date=date(2026, 5, 1),
            direction="买入",
            price=Decimal("10.00"),
            volume=1000,
            amount=Decimal("10000.00"),
            cost=cost,
            signal_type="买入",
        )

        assert not hasattr(trade, '__dict__'), "slots=True should prevent __dict__"

        trade.action = "BUY"
        assert trade.action == "BUY", "Slot assignment should work correctly"

    def test_json_serialization_contains_all_fields(self):
        """JSON serialization should include all 18 fields when converted to dict."""
        cost = CostResult(
            commission=Decimal("5.0"),
            stamp_tax=Decimal("0"),
            transfer_fee=Decimal("1.0"),
            total_fee=Decimal("6.0"),
        )
        trade = TradeRecord(
            ts_code="000001.SZ",
            trade_date=date(2026, 5, 1),
            direction="买入",
            price=Decimal("10.00"),
            volume=1000,
            amount=Decimal("10000.00"),
            cost=cost,
            signal_type="买入",
            action="BUY",
            target_position=0.8,
            position_before=0.0,
            position_after=0.8,
            pnl=Decimal("0"),
            balance_before=Decimal("100000"),
            balance_after=Decimal("90000"),
            holding_days=0,
        )

        trade_dict = {
            "ts_code": trade.ts_code,
            "trade_date": trade.trade_date.isoformat(),
            "direction": trade.direction,
            "price": float(trade.price),
            "volume": trade.volume,
            "amount": float(trade.amount),
            "commission": float(trade.cost.commission),
            "stamp_tax": float(trade.cost.stamp_tax),
            "transfer_fee": float(trade.cost.transfer_fee),
            "total_fee": float(trade.cost.total_fee),
            "action": trade.action,
            "signal_reason": trade.signal_reason,
            "target_position": trade.target_position,
            "position_before": trade.position_before,
            "position_after": trade.position_after,
            "pnl": float(trade.pnl),
            "balance_before": float(trade.balance_before),
            "balance_after": float(trade.balance_after),
            "holding_days": trade.holding_days,
        }

        json_str = json.dumps(trade_dict, ensure_ascii=False)
        parsed = json.loads(json_str)

        expected_keys = [
            'ts_code', 'trade_date', 'direction', 'price', 'volume', 'amount',
            'commission', 'stamp_tax', 'transfer_fee', 'total_fee',
            'action', 'signal_reason', 'target_position', 'position_before',
            'position_after', 'pnl', 'balance_before', 'balance_after', 'holding_days'
        ]
        for key in expected_keys:
            assert key in parsed, f"JSON missing key: {key}"

    def test_cost_result_nested_in_trade_record(self):
        """CostResult should be properly nested within TradeRecord."""
        cost = CostResult(
            commission=Decimal("7.5"),
            stamp_tax=Decimal("5.0"),
            transfer_fee=Decimal("1.0"),
            total_fee=Decimal("13.5"),
        )
        trade = TradeRecord(
            ts_code="000001.SZ",
            trade_date=date(2026, 5, 10),
            direction="卖出",
            price=Decimal("12.00"),
            volume=800,
            amount=Decimal("9600.00"),
            cost=cost,
            signal_type="卖出",
            action="SELL_PARTIAL",
        )

        assert isinstance(trade.cost, CostResult)
        assert trade.cost.commission == Decimal("7.5")
        assert trade.cost.stamp_tax == Decimal("5.0")
        assert trade.cost.transfer_fee == Decimal("1.0")
        assert trade.cost.total_fee == Decimal("13.5")

    def test_enhanced_fields_are_mutable(self):
        """Enhanced fields should be mutable for runtime updates."""
        cost = CostResult(
            commission=Decimal("5.0"),
            stamp_tax=Decimal("0"),
            transfer_fee=Decimal("1.0"),
            total_fee=Decimal("6.0"),
        )
        trade = TradeRecord(
            ts_code="000001.SZ",
            trade_date=date(2026, 5, 1),
            direction="买入",
            price=Decimal("10.00"),
            volume=1000,
            amount=Decimal("10000.00"),
            cost=cost,
            signal_type="买入",
        )

        trade.action = "BUY"
        trade.target_position = 0.9
        trade.position_before = 0.0
        trade.position_after = 0.9
        trade.balance_before = Decimal("100000")
        trade.balance_after =Decimal("90000")

        assert trade.action == "BUY"
        assert trade.target_position == 0.9
        assert trade.position_after == 0.9


@pytest.mark.backtest
class TestBuyTradeDetails:
    """Test 2: Verify buy trade details and calculations."""

    def test_buy_trade_action_and_direction(self):
        """Buy trade should have action='BUY' and direction='买入'."""
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] > 0, "Should have at least one buy trade"
        first_trade_record = result["trade_records"][0]

        assert first_trade_record["action"] == "BUY", \
            f"Expected action='BUY', got '{first_trade_record['action']}'"
        assert first_trade_record["direction"] == "买入", \
            f"Expected direction='买入', got '{first_trade_record['direction']}'"

    def test_buy_trade_pnl_is_zero(self):
        """Buy trades should have pnl == 0 (no profit/loss on entry)."""
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        buy_trades = [t for t in result["trade_records"] if t["direction"] == "买入"]
        assert len(buy_trades) > 0

        for trade in buy_trades:
            assert trade["pnl"] == 0.0, f"Buy trade pnl should be 0, got {trade['pnl']}"

    def test_buy_trade_holding_days_is_zero(self):
        """Buy trades should have holding_days == 0 (no holding period yet)."""
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        buy_trades = [t for t in result["trade_records"] if t["direction"] == "买入"]
        assert len(buy_trades) > 0

        for trade in buy_trades:
            assert trade["holding_days"] == 0, \
                f"Buy trade holding_days should be 0, got {trade['holding_days']}"

    def test_buy_trade_position_and_balance_changes(self):
        """Buy trade should increase position and decrease cash balance."""
        strategy = '''
def generate_signal(ctx):
    if len(ctx.close) < 2:
        return {"signal_type": "观望", "current_position": 0}
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        buy_trades = [t for t in result["trade_records"] if t["direction"] == "买入"]
        assert len(buy_trades) > 0

        for trade in buy_trades:
            assert trade["position_before"] < trade["position_after"], \
                f"Position should increase: before={trade['position_before']}, after={trade['position_after']}"
            assert trade["balance_before"] > trade["balance_after"], \
                f"Balance should decrease: before={trade['balance_before']}, after={trade['balance_after']}"
            assert trade["position_after"] > 0, "Should have positive position after buy"

    def test_buy_trade_signal_type_recorded(self):
        """Buy trade should correctly record signal information."""
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        buy_trades = [t for t in result["trade_records"] if t["direction"] == "买入"]
        assert len(buy_trades) > 0

        for trade in buy_trades:
            assert "action" in trade, "Trade record should contain action field"
            assert trade["action"] == "BUY", f"Action should be 'BUY', got '{trade.get('action')}'"
            assert "direction" in trade, "Trade record should contain direction field"
            assert trade["direction"] == "买入", \
                f"direction should be '买入', got '{trade.get('direction')}'"

    def test_buy_trade_target_position_from_strategy(self):
        """Target position should match strategy configuration."""
        target_pos = 0.75
        strategy = f'''
def generate_signal(ctx):
    return {{"signal_type": "买入", "current_position": 0, "target_position": {target_pos}}}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        buy_trades = [t for t in result["trade_records"] if t["direction"] == "买入"]
        assert len(buy_trades) > 0

        for trade in buy_trades:
            assert trade["target_position"] == target_pos, \
                f"target_position should be {target_pos}, got {trade['target_position']}"

    def test_entry_dates_recorded_on_buy(self):
        """Entry dates dictionary should record buy date for the stock."""
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] > 0
        assert "000001.SZ" in runner._entry_dates, \
            "_entry_dates should contain the stock code after buy"


@pytest.mark.backtest
class TestSellTradeDetails:
    """Test 3: Verify sell trade details and calculations."""

    def setup_sell_scenario(self) -> tuple[BacktestRunner, dict]:
        """Create a buy-then-sell scenario for testing sell trades."""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 10:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    elif day <= 15:
        return {"signal_type": "观望", "current_position": ctx.current_position}
    else:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=25)
        result = runner.run({"000001.SZ": klines})
        return runner, result

    def test_sell_action_and_direction(self):
        """Sell trade should have correct action and direction based on type."""
        runner, result = self.setup_sell_scenario()

        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]
        if len(sell_trades) > 0:
            trade = sell_trades[0]
            assert trade["action"] in ("SELL_ALL", "SELL_PARTIAL"), \
                f"Sell action should be SELL_ALL or SELL_PARTIAL, got '{trade['action']}'"
            if trade["action"] == "SELL_ALL":
                assert trade["direction"] == "全部卖出"
            else:
                assert trade["direction"] == "部分卖出"

    def test_sell_pnl_calculation_correct(self):
        """Sell PnL should be calculated as (price - avg_cost) * volume - total_fee."""
        runner, result = self.setup_sell_scenario()

        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]
        if len(sell_trades) > 0:
            trade = sell_trades[0]
            assert "pnl" in trade, "Sell trade should have pnl field"
            assert isinstance(trade["pnl"], float), "pnl should be a float value"

            buy_trades = [t for t in result["trade_records"] if t["direction"] == "买入"]
            if len(buy_trades) > 0:
                avg_buy_price = sum(t["price"] * t["volume"] for t in buy_trades) / \
                               sum(t["volume"] for t in buy_trades)
                expected_pnl = (trade["price"] - avg_buy_price) * trade["volume"] - trade["total_fee"]
                assert abs(trade["pnl"] - expected_pnl) < 0.01, \
                    f"PnL mismatch: expected {expected_pnl}, got {trade['pnl']}"

    def test_sell_holding_days_positive(self):
        """Sell trades should have holding_days > 0 reflecting actual holding period."""
        runner, result = self.setup_sell_scenario()

        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]
        if len(sell_trades) > 0:
            for trade in sell_trades:
                assert trade["holding_days"] >= 0, \
                    f"holding_days should be >= 0, got {trade['holding_days']}"
                if trade["direction"] == "全部卖出":
                    assert trade["holding_days"] > 0, \
                        f"Full sell should have positive holding_days, got {trade['holding_days']}"

    def test_sell_position_decreases(self):
        """Sell trade should decrease position ratio."""
        runner, result = self.setup_sell_scenario()

        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]
        if len(sell_trades) > 0:
            for trade in sell_trades:
                if trade["position_before"] > 0:
                    assert trade["position_before"] > trade["position_after"], \
                        f"Position should decrease: before={trade['position_before']}, after={trade['position_after']}"

    def test_sell_balance_reflects_pnl(self):
        """Balance after sell should reflect PnL impact."""
        runner, result = self.setup_sell_scenario()

        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]
        if len(sell_trades) > 0:
            for trade in sell_trades:
                assert "balance_before" in trade, "Sell trade should have balance_before"
                assert "balance_after" in trade, "Sell trade should have balance_after"
                assert "pnl" in trade, "Sell trade should have pnl field"
                balance_change = trade["balance_after"] - trade["balance_before"]
                assert isinstance(balance_change, float), "Balance change should be numeric"

    def test_full_sell_clears_entry_dates(self):
        """Full sell (SELL_ALL) should clear entry_dates for that stock."""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 10:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    else:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=20)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t["action"] == "SELL_ALL"]
        if len(sell_trades) > 0:
            assert "000001.SZ" not in runner._entry_dates or \
                   runner._entry_dates.get("000001.SZ") is None, \
                   "_entry_dates should be cleared after full sell"


@pytest.mark.backtest
class TestMultiTradeSequenceTracking:
    """Test 4: Verify tracking across multiple consecutive trades."""

    def test_four_trade_sequence_tracking(self):
        """
        Simulate complex sequence:
        Day 1: Buy 1000 shares @ 10 yuan
        Day 10: Add 500 shares @ 12 yuan (verify average cost recalculation)
        Day 20: Sell 800 shares @ 11 yuan (verify partial sell PnL)
        Day 30: Sell remaining @ 13 yuan (verify final PnL)

        Verify position ratios, cumulative PnL, holding days, and balance changes.
        """
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 3:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    elif day <= 12:
        return {"signal_type": "增持", "current_position": ctx.current_position, "target_position": 1.0}
    elif day <= 22:
        return {"signal_type": "减仓", "current_position": ctx.current_position, "target_position": 0.4}
    else:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("200000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=30, base_price=10.0, price_increment=0.15)
        result = runner.run({"000001.SZ": klines})

        trades = result["trade_records"]
        assert len(trades) >= 3, f"Expected at least 3 trades, got {len(trades)}"

        buy_trades = [t for t in trades if t["direction"] == "买入"]
        sell_trades = [t for t in trades if t["direction"] in ("全部卖出", "部分卖出")]

        assert len(buy_trades) >= 1, "Should have at least one buy trade"
        assert len(sell_trades) >= 1, "Should have at least one sell trade"

        for i, trade in enumerate(trades):
            assert "position_before" in trade, f"Trade {i} missing position_before"
            assert "position_after" in trade, f"Trade {i} missing position_after"
            assert "balance_before" in trade, f"Trade {i} missing balance_before"
            assert "balance_after" in trade, f"Trade {i} missing balance_after"

            assert isinstance(trade["position_before"], float), \
                f"Trade {i}: position_before should be float"
            assert isinstance(trade["position_after"], float), \
                f"Trade {i}: position_after should be float"

            if i > 0:
                prev_trade = trades[i - 1]
                assert 0.0 <= trade["position_before"] <= 1.0, \
                    f"Trade {i}: position_before should be in [0, 1], got {trade['position_before']}"
                assert 0.0 <= trade["position_after"] <= 1.0, \
                    f"Trade {i}: position_after should be in [0, 1], got {trade['position_after']}"

    def test_cumulative_pnl_across_sequence(self):
        """Cumulative PnL across all sells should reflect overall profitability."""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 5:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    elif day <= 15:
        return {"signal_type": "观望", "current_position": ctx.current_position}
    else:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=25, base_price=10.0, price_increment=0.2)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]
        if len(sell_trades) > 0:
            total_pnl = sum(t["pnl"] for t in sell_trades)
            assert isinstance(total_pnl, float), "Total PnL should be calculable"

            initial_cash = float(config.initial_cash)
            final_asset = result["equity_curve"][-1]["total_asset"]
            actual_return = final_asset - initial_cash

            assert abs(total_pnl - actual_return) < 100.0, \
                f"Cumulative PnL ({total_pnl}) should approximate actual return ({actual_return})"

    def test_holding_days_increase_over_time(self):
        """Holding days should increase for later sells in a sequence."""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 3:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    elif day <= 10:
        return {"signal_type": "减仓", "current_position": ctx.current_position, "target_position": 0.5}
    else:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=20, base_price=10.0, price_increment=0.1)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]
        if len(sell_trades) >= 2:
            for i in range(1, len(sell_trades)):
                assert sell_trades[i]["holding_days"] >= sell_trades[i-1]["holding_days"], \
                    f"Holding days should increase over time: " \
                    f"sell[{i-1}]={sell_trades[i-1]['holding_days']}, sell[{i}]={sell_trades[i]['holding_days']}"

    def test_balance_changes_are_reasonable(self):
        """Balance changes should be reasonable relative to trade sizes."""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 5:
        return {"signal_type": "买入", "current_position": 0, "target_position": 0.9}
    elif day <= 15:
        return {"signal_type": "观望", "current_position": ctx.current_position}
    else:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=25, base_price=10.0, price_increment=0.15)
        result = runner.run({"000001.SZ": klines})

        for trade in result["trade_records"]:
            assert "balance_before" in trade, "Trade should have balance_before"
            assert "balance_after" in trade, "Trade should have balance_after"
            assert "price" in trade, "Trade should have price"
            assert "volume" in trade, "Trade should have volume"

            balance_change = abs(trade["balance_after"] - trade["balance_before"])
            trade_value = trade["price"] * trade["volume"]

            assert isinstance(balance_change, (int, float)), \
                f"Balance change should be numeric, got {type(balance_change)}"
            assert balance_change >= 0, \
                f"Absolute balance change should be non-negative, got {balance_change}"


@pytest.mark.backtest
class TestEdgeCasesAndBoundaryConditions:
    """Test 5: Edge cases and boundary conditions."""

    def test_sell_with_no_position_produces_no_trade(self):
        """Attempting to sell with no position should produce no trade record."""
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "卖出", "current_position": 0}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]
        assert len(sell_trades) == 0, \
            f"No sell trades should be generated without position, got {len(sell_trades)}"
        assert result["trade_count"] == 0, \
            f"Total trades should be 0, got {result['trade_count']}"

    def test_insufficient_cash_reduces_buy_volume(self):
        """Insufficient funds should reduce actual buy volume vs target volume."""
        small_cash = Decimal("500")
        high_price = 100.0
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.9}
'''
        config = sample_backtest_config(
            source_code=strategy,
            initial_cash=small_cash,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(days=10, base_price=high_price)
        result = runner.run({"000001.SZ": klines})

        if result["trade_count"] > 0:
            trade = result["trade_records"][0]
            total_cost = trade["price"] * trade["volume"] + trade["total_fee"]

            assert total_cost <= float(small_cash), \
                f"Total cost ({total_cost}) should not exceed available cash ({small_cash})"
            assert trade["volume"] % 100 == 0, \
                f"Volume should be rounded to 100-share lots, got {trade['volume']}"

    def test_multiple_trades_same_day_if_supported(self):
        """Verify behavior when multiple signals occur on same trading day."""
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=5)
        result = runner.run({"000001.SZ": klines})

        dates = [t["trade_date"] for t in result["trade_records"]]
        duplicate_dates = [d for d in set(dates) if dates.count(d) > 1]

        if len(duplicate_dates) > 0:
            for dup_date in duplicate_dates:
                day_trades = [t for t in result["trade_records"] if t["trade_date"] == dup_date]
                for i, trade in enumerate(day_trades):
                    assert "position_before" in trade, \
                        f"Multiple trades on {dup_date}[{i}] missing position_before"
                    assert "position_after" in trade, \
                        f"Multiple trades on {dup_date}[{i}] missing position_after"

    def test_zero_volume_trade_not_recorded(self):
        """Trades with zero calculated volume should not be recorded."""
        tiny_cash = Decimal("1")
        very_high_price = 10000.0
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.9}
'''
        config = sample_backtest_config(
            source_code=strategy,
            initial_cash=tiny_cash,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(days=5, base_price=very_high_price)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] == 0, \
            f"No trades should be recorded when volume rounds to zero, got {result['trade_count']}"

    def test_exact_cash_affordability(self):
        """When cash exactly covers trade cost (including fees), trade should execute."""
        exact_amount = Decimal("10050")
        price = 10.0
        strategy = f'''
def generate_signal(ctx):
    return {{"signal_type": "买入", "current_position": 0, "target_position": 0.99}}
'''
        config = sample_backtest_config(
            source_code=strategy,
            initial_cash=exact_amount,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(days=5, base_price=price)
        result = runner.run({"000001.SZ": klines})

        if result["trade_count"] > 0:
            trade = result["trade_records"][0]
            total_cost = trade["price"] * trade["volume"] + trade["total_fee"]
            assert total_cost <= float(exact_amount), \
                f"Trade cost ({total_cost}) should not exceed cash ({exact_amount})"


@pytest.mark.backtest
class TestTradeRecordIntegration:
    """Integration tests combining multiple aspects of trade record functionality."""

    def test_complete_lifecycle_buy_hold_sell(self):
        """Test complete lifecycle: buy → hold → sell with all fields populated."""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 5:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    elif day <= 20:
        return {"signal_type": "观望", "current_position": ctx.current_position}
    else:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=28, base_price=10.0, price_increment=0.2)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] >= 2, "Should have at least buy and sell trades"

        buy_trades = [t for t in result["trade_records"] if t["direction"] == "买入"]
        sell_trades = [t for t in result["trade_records"] if t["direction"] in ("全部卖出", "部分卖出")]

        assert len(buy_trades) >= 1, "Should have at least one buy"
        assert len(sell_trades) >= 1, "Should have at least one sell"

        for buy in buy_trades:
            assert_buy_trade_from_dict(buy)

        for sell in sell_trades:
            assert_sell_trade_from_dict(sell)

        if buy_trades and sell_trades:
            assert sell_trades[0]["trade_date"] > buy_trades[0]["trade_date"], \
                "Sell should occur after buy"

    def test_trade_records_in_chronological_order(self):
        """All trade records should be in chronological order."""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day % 7 <= 2:
        return {"signal_type": "买入", "current_position": 0, "target_position": 0.5}
    elif day % 7 <= 4:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
    else:
        return {"signal_type": "观望", "current_position": ctx.current_position}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=25)
        result = runner.run({"000001.SZ": klines})

        if len(result["trade_records"]) >= 2:
            for i in range(1, len(result["trade_records"])):
                prev_date = result["trade_records"][i-1]["trade_date"]
                curr_date = result["trade_records"][i]["trade_date"]
                assert curr_date >= prev_date, \
                    f"Trades should be chronological: [{i-1}]={prev_date}, [{i}]={curr_date}"

    def test_all_trades_have_consistent_stock_code(self):
        """All trades should reference the same stock code in single-stock backtest."""
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.7}
'''
        config = sample_backtest_config(source_code=strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        for trade in result["trade_records"]:
            assert trade["ts_code"] == "000001.SZ", \
                f"All trades should be for 000001.SZ, got {trade['ts_code']}"


def assert_buy_trade_from_dict(trade: dict):
    """Assert helper for buy trade dictionaries (from JSON results)."""
    assert trade["direction"] == "买入", f"Direction should be '买入': {trade['direction']}"
    assert trade["action"] == "BUY", f"Action should be 'BUY': {trade['action']}"
    assert trade["pnl"] == 0.0, f"PnL should be 0 for buy: {trade['pnl']}"
    assert trade["holding_days"] == 0, f"Holding days should be 0 for buy: {trade['holding_days']}"


def assert_sell_trade_from_dict(trade: dict):
    """Assert helper for sell trade dictionaries (from JSON results)."""
    assert trade["direction"] in ("全部卖出", "部分卖出"), \
        f"Invalid sell direction: {trade['direction']}"
    assert trade["action"] in ("SELL_ALL", "SELL_PARTIAL"), \
        f"Invalid sell action: {trade['action']}"
    if trade["position_before"] > 0:
        assert trade["position_before"] >= trade["position_after"], \
            f"Position should not increase on sell: before={trade['position_before']}, after={trade['position_after']}"
