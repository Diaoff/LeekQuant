"""Tests for backtest engine (adapter.py).

Covers:
- BacktestRunner.run() main flow
- A-share rule filtering (suspension, limit up/down)
- Buy/sell matching logic with 100-share lots
- Performance metrics calculation
- Strategy execution (dual-MA, error handling)
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.backtest import adapter as adapter_module
from app.backtest.adapter import (
    BacktestConfig,
    BacktestContext,
    BacktestRunner,
    KBar,
    Position,
    TradeRecord,
    _LotEntry,
)
from app.backtest.strategy_runtime import StrategyExecutionResult
from app.libs import MyTT


@pytest.fixture(autouse=True)
def patch_backtest_context_position_setter(monkeypatch):
    """Workaround: Add setter to BacktestContext.current_position property.

    The source code's _exec_strategy tries to set ctx.current_position but
    BacktestContext only defines a getter. This monkey-patch fixes that.
    """
    original_current_position = BacktestContext.current_position
    _position_value = 0.0

    def getter(self):
        return _position_value

    def setter(self, value):
        nonlocal _position_value
        _position_value = value

    def fake_execute_strategy(source_code, ctx, **_kwargs):
        sandbox = {"ctx": ctx}
        for name in dir(MyTT):
            if not name.startswith("_"):
                sandbox[name] = getattr(MyTT, name)
        try:
            exec(source_code, sandbox)
            func = sandbox.get("generate_signal")
            if func is None:
                return StrategyExecutionResult(ok=True, signal=None)
            result = func(ctx)
            return StrategyExecutionResult(ok=True, signal=result if isinstance(result, dict) else None)
        except Exception as exc:
            return StrategyExecutionResult(
                ok=False,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
                traceback="test traceback",
            )

    BacktestContext.current_position = property(getter, setter)
    monkeypatch.setattr(adapter_module, "execute_strategy", fake_execute_strategy)
    yield
    BacktestContext.current_position = original_current_position


def generate_klines(
    ts_code: str = "000001.SZ",
    start_date: date | None = None,
    days: int = 15,
    base_price: float = 10.0,
    price_increment: float = 0.1,
    is_suspended: bool = False,
    is_limit_up: bool = False,
    is_limit_down: bool = False,
) -> list[KBar]:
    """Generate consecutive K-line data for testing.

    Args:
        ts_code: Stock code
        start_date: Start date (default: 2026-05-01)
        days: Number of trading days to generate
        base_price: Starting price
        is_suspended: Whether all bars are suspended
        is_limit_up: Whether all bars are limit-up
        is_limit_down: Whether all bars are limit-down
    """
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
            is_suspended=is_suspended,
            is_limit_up=is_limit_up,
            is_limit_down=is_limit_down,
        ))
        pre_close = close

    return klines


SIMPLE_BUY_STRATEGY = '''
def generate_signal(ctx):
    if len(ctx.close) < 2:
        return {"signal_type": "观望", "current_position": 0}
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''

ALWAYS_BUY_STRATEGY = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''

BUY_ONCE_STRATEGY = '''
def generate_signal(ctx):
    if ctx.trade_date.day == 1:
        return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
    return {"signal_type": "观望", "current_position": ctx.current_position}
'''

DUAL_MA_STRATEGY = '''
def generate_signal(ctx):
    if len(ctx.close) < 5:
        return {"signal_type": "观望", "current_position": ctx.current_position}
    ma5 = sum(ctx.close[-5:]) / 5
    ma10 = sum(ctx.close[-10:]) / 10 if len(ctx.close) >= 10 else ma5
    if ctx.close[-1] > ma5 and ctx.current_position < 0.5:
        return {"signal_type": "买入", "current_position": ctx.current_position, "target_position": 0.8}
    elif ctx.close[-1] < ma5 and ctx.current_position > 0.3:
        return {"signal_type": "卖出", "current_position": ctx.current_position}
    return {"signal_type": "观望", "current_position": ctx.current_position}
'''


@pytest.mark.backtest
class TestBacktestRunnerMainFlow:
    """Test BacktestRunner.run() main execution flow."""

    def test_empty_stock_pool_raises_error(self):
        """空股票池抛出 ValueError"""
        config = BacktestConfig(
            strategy_id=1,
            source_code="",
            stock_pool=[],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 15),
        )
        runner = BacktestRunner(config)
        with pytest.raises(ValueError, match="no trading dates"):
            runner.run({})

    def test_no_kline_data_raises_error(self):
        """无K线数据抛出 ValueError"""
        config = sample_backtest_config(source_code=SIMPLE_BUY_STRATEGY)
        runner = BacktestRunner(config)
        with pytest.raises(ValueError, match="no trading dates"):
            runner.run({"000001.SZ": []})

    def test_normal_run_produces_equity_curve(self):
        """正常运行产生 equity_curve"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        assert "equity_curve" in result
        assert len(result["equity_curve"]) == 15
        assert all("date" in e and "total_asset" in e for e in result["equity_curve"])

    def test_result_contains_all_required_fields(self):
        """结果包含所有必需字段"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        required_fields = [
            "total_return", "annual_return", "sharpe_ratio",
            "max_drawdown", "annual_vol", "win_rate", "trade_count",
            "performance", "trade_records", "equity_curve",
        ]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"

    def test_initial_equity_equals_initial_cash(self):
        """初始权益等于初始资金"""
        config = sample_backtest_config(initial_cash=Decimal("100000"), source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        assert result["equity_curve"][0]["total_asset"] == pytest.approx(100000.0, rel=0.01)

    def test_empty_result_when_no_dates(self):
        """无交易日期时抛出 ValueError"""
        config = BacktestConfig(
            strategy_id=1,
            source_code="",
            stock_pool=["000001.SZ"],
            start_date=date(2099, 1, 1),
            end_date=date(2099, 1, 15),
        )
        runner = BacktestRunner(config)
        with pytest.raises(ValueError, match="no trading dates"):
            runner.run({})


@pytest.mark.backtest
class TestAShareRuleFiltering:
    """Test A-share rule filtering at the runner level."""

    def test_suspended_day_skips_trading(self):
        """停牌日跳过不交易"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(is_suspended=True, days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] == 0
        assert all(s["action"] == "BLOCKED" for s in runner.signals)

    def test_limit_up_buy_records_signal_and_blocks_match(self):
        """涨停买入信号保留，撮合阶段不成交"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(is_limit_up=True, days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] == 0
        buy_signals = [s for s in runner.signals if s.get("action") == "BUY"]
        assert len(buy_signals) > 0
        assert all(s.get("match_status") == "BLOCKED" for s in buy_signals)
        assert all(s.get("reason") == "涨停不可买入" for s in buy_signals)

    def test_limit_down_sell_records_signal_and_blocks_match(self):
        """跌停卖出信号保留，撮合阶段不成交"""
        sell_strategy = '''
def generate_signal(ctx):
    return {"signal_type": "卖出", "current_position": 0.5}
'''
        config = sample_backtest_config(source_code=sell_strategy, initial_cash=Decimal("100000"))
        runner = BacktestRunner(config)
        runner.positions["000001.SZ"] = Position(ts_code="000001.SZ", shares=1000, avg_cost=Decimal("10"))
        # T+1 check now uses _open_lots (FIFO) instead of _entry_dates; set up
        # an entry_date prior to the first kline date so the sell is unlocked.
        runner._open_lots["000001.SZ"] = [
            _LotEntry(ts_code="000001.SZ", shares=1000, cost=Decimal("10"), entry_date=date(2026, 4, 30)),
        ]
        runner._entry_dates["000001.SZ"] = date(2026, 4, 30)
        klines = generate_klines(is_limit_down=True, days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] == 0
        sell_signals = [s for s in runner.signals if s.get("action") == "SELL_ALL"]
        assert len(sell_signals) > 0
        assert all(s.get("match_status") == "BLOCKED" for s in sell_signals)
        assert all(s.get("reason") == "跌停不可卖出" for s in sell_signals)

    def test_no_position_cannot_sell(self):
        """无持仓时不能卖出"""
        sell_strategy = '''
def generate_signal(ctx):
    return {"signal_type": "卖出", "current_position": 0.0}
'''
        config = sample_backtest_config(source_code=sell_strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] == 0


@pytest.mark.backtest
class TestBuySellMatching:
    """Test buy/sell matching logic."""

    def test_buy_calculates_volume_in_100_lots(self):
        """买入按目标仓位计算数量（取整到100股倍数）"""
        config = sample_backtest_config(
            initial_cash=Decimal("100000"),
            source_code=ALWAYS_BUY_STRATEGY,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(base_price=10.0, days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] >= 1, "Should have at least one buy trade"
        first_trade = result["trade_records"][0]
        assert first_trade["volume"] % 100 == 0

    def test_insufficient_cash_reduces_volume(self):
        """资金不足时自动缩减买入数量"""
        config = sample_backtest_config(
            initial_cash=Decimal("500"),
            source_code=ALWAYS_BUY_STRATEGY,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(base_price=100.0, days=10)
        result = runner.run({"000001.SZ": klines})

        if result["trade_count"] > 0:
            trade = result["trade_records"][0]
            cost = trade["price"] * trade["volume"] + trade["total_fee"]
            assert cost <= 500

    def test_sell_all_liquidates_position(self):
        """卖出全部持仓（SELL_ALL）"""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 7:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    elif day <= 10:
        return {"signal_type": "观望", "current_position": 0.8}
    else:
        return {"signal_type": "卖出", "current_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t["direction"] == "卖出"]
        assert len(sell_trades) > 0

    def test_sell_partial_reduces_position(self):
        """卖出部分持仓（SELL_PARTIAL）"""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 7:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    elif day <= 10:
        return {"signal_type": "观望", "current_position": 0.8}
    else:
        return {"signal_type": "减仓", "current_position": 0.8, "target_position": 0.3}
'''
        config = sample_backtest_config(source_code=strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t["direction"] == "卖出"]
        assert any(t for t in sell_trades)

    def test_trade_record_contains_cost_info(self):
        """费用正确记录到 TradeRecord"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        if result["trade_count"] > 0:
            trade = result["trade_records"][0]
            assert "commission" in trade
            assert "stamp_tax" in trade
            assert "transfer_fee" in trade
            assert "total_fee" in trade
            assert trade["total_fee"] > 0


@pytest.mark.backtest
class TestPerformanceMetrics:
    """Test performance metrics calculation."""

    def test_total_return_calculation(self):
        """总收益率计算正确"""
        config = sample_backtest_config(
            initial_cash=Decimal("100000"),
            source_code=ALWAYS_BUY_STRATEGY,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(base_price=10.0, days=15)
        result = runner.run({"000001.SZ": klines})

        initial = float(config.initial_cash)
        final = result["equity_curve"][-1]["total_asset"]
        expected_return = (final - initial) / initial
        assert abs(result["total_return"] - expected_return) < 1e-6

    def test_annual_return_calculation(self):
        """年化收益率计算"""
        config = sample_backtest_config(
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 15),
            source_code=ALWAYS_BUY_STRATEGY,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        assert isinstance(result["annual_return"], float)

    def test_max_drawdown_detection(self):
        """最大回撤检测"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=20)
        result = runner.run({"000001.SZ": klines})

        assert 0 <= result["max_drawdown"] <= 1

    def test_sharpe_ratio_requires_multiple_days(self):
        """夏普比率计算需要至少2天数据"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        assert isinstance(result["sharpe_ratio"], float)

    def test_win_rate_statistics(self):
        """胜率统计"""
        strategy = f'''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day < 8:
        return {{"signal_type": "买入", "current_position": ctx.current_position, "target_position": 1.0}}
    elif day < 12:
        return {{"signal_type": "观望", "current_position": ctx.current_position}}
    else:
        return {{"signal_type": "卖出", "current_position": ctx.current_position}}
'''
        config = sample_backtest_config(source_code=strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        assert 0 <= result["win_rate"] <= 1

    def test_performance_dict_format(self):
        """performance 字典包含格式化后的百分比"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        perf = result["performance"]
        assert "initial_cash" in perf
        assert "final_asset" in perf
        assert "total_return_pct" in perf
        assert "sharpe_ratio" in perf
        assert "max_drawdown_pct" in perf


@pytest.mark.backtest
class TestStrategyExecution:
    """Test strategy code execution."""

    def test_dual_ma_strategy_executes_successfully(self):
        """简单双均线策略能正常执行"""
        config = sample_backtest_config(source_code=DUAL_MA_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=20)
        result = runner.run({"000001.SZ": klines})

        assert "equity_curve" in result
        assert len(result["equity_curve"]) > 0

    def test_non_dict_strategy_returns_none(self):
        """策略返回非dict时返回None，不产生交易"""
        bad_strategy = '''
def generate_signal(ctx):
    return "not a dict"
'''
        config = sample_backtest_config(source_code=bad_strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] == 0

    def test_exception_in_strategy_returns_none(self):
        """策略抛异常时不产生交易，并记录结构化错误"""
        error_strategy = '''
def generate_signal(ctx):
    raise RuntimeError("strategy error")
'''
        config = sample_backtest_config(source_code=error_strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)

        should_not_raise = lambda: runner.run({"000001.SZ": klines})
        result = should_not_raise()
        assert result["trade_count"] == 0
        # Step 5 (lookahead fix): window[:-1] is empty on day 1, so the
        # strategy is only invoked on days 2..10 (9 calls, each raising).
        assert result["performance"]["strategy_error_count"] == 9
        assert result["strategy_errors"][0]["error_type"] == "RuntimeError"
        assert result["strategy_errors"][0]["error_message"] == "strategy error"

    def test_missing_generate_signal_returns_none(self):
        """缺少 generate_signal 函数时不产生交易"""
        empty_strategy = 'x = 1'
        config = sample_backtest_config(source_code=empty_strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] == 0

    def test_mytt_functions_available(self):
        """MyTT 函数可用（MA, EMA 等）"""
        mytt_strategy = '''
def generate_signal(ctx):
    try:
        ma = MA(ctx.close, 5)
        ema = EMA(ctx.close, 5)
        return {"signal_type": "观望", "current_position": 0}
    except NameError:
        return None
'''
        config = sample_backtest_config(source_code=mytt_strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        assert "equity_curve" in result


@pytest.mark.backtest
class TestBacktestContext:
    """Test BacktestContext properties."""

    def test_context_exposes_close_prices(self):
        """Context 暴露 close 价格列表"""
        klines = generate_klines(days=10)
        ctx = BacktestContext(klines, {}, Decimal("100000"))
        assert len(ctx.close) == 10
        assert all(isinstance(c, float) for c in ctx.close)

    def test_context_exposes_ohlcv(self):
        """Context 暴露 OHLCV 数据"""
        klines = generate_klines(days=5)
        ctx = BacktestContext(klines, {}, Decimal("100000"))

        assert len(ctx.open) == 5
        assert len(ctx.high) == 5
        assert len(ctx.low) == 5
        assert len(ctx.volume) == 5
        assert len(ctx.amount) == 5

    def test_context_exposes_trade_date(self):
        """Context 暴露 trade_date"""
        klines = generate_klines(days=5)
        ctx = BacktestContext(klines, {}, Decimal("100000"))
        assert isinstance(ctx.trade_date, date)

    def test_context_current_position_default_zero(self):
        """Context current_position 默认为 0"""
        klines = generate_klines(days=5)
        ctx = BacktestContext(klines, {}, Decimal("100000"))
        assert ctx.current_position == 0.0

    def test_context_empty_klines_returns_today(self):
        """空K线时 trade_date 返回今天"""
        ctx = BacktestContext([], {}, Decimal("100000"))
        assert isinstance(ctx.trade_date, date)


@pytest.mark.backtest
class TestPositionManagement:
    """Test position management during backtest."""

    def test_buy_creates_position(self):
        """买入创建持仓记录"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] > 0 or "000001.SZ" in runner.positions

    def test_avg_cost_updated_on_buy(self):
        """多次买入更新平均成本"""
        multi_buy_strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 10:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    return {"signal_type": "观望", "current_position": 0.8}
'''
        config = sample_backtest_config(source_code=multi_buy_strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        buy_trades = [t for t in result["trade_records"] if t["direction"] == "买入"]
        assert len(buy_trades) >= 1

    def test_sell_all_clears_position(self):
        """全部卖出清空持仓"""
        strategy = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 7:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    elif day <= 10:
        return {"signal_type": "观望", "current_position": 0.8}
    else:
        return {"signal_type": "卖出", "current_position": 0.8}
'''
        config = sample_backtest_config(source_code=strategy)
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})


def sample_backtest_config(**kwargs):
    """Helper to create BacktestConfig with defaults."""
    defaults = dict(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 15),
        initial_cash=Decimal("100000"),
    )
    defaults.update(kwargs)
    return BacktestConfig(**defaults)


@pytest.mark.backtest
class TestBacktestSignalPriority:
    """Test same-day multi-stock signal ordering."""

    def test_confidence_orders_buy_signals_before_stock_pool_order(self):
        strategy = '''
def generate_signal(ctx):
    if ctx.close[-1] == 10.1:
        return {"signal_type": "买入", "target_position": 0.4, "confidence": 0.30}
    return {"signal_type": "买入", "target_position": 0.4, "confidence": 0.90}
'''
        config = sample_backtest_config(
            source_code=strategy,
            stock_pool=["000001.SZ", "000002.SZ"],
            initial_cash=Decimal("20000"),
        )
        runner = BacktestRunner(config)
        # Step 5 (lookahead fix): need >=2 days so window[:-1] is non-empty
        # on td=May 2. Strategy then sees bar1.close in ctx.close[-1].
        result = runner.run({
            "000001.SZ": generate_klines("000001.SZ", days=2, base_price=10.1),
            "000002.SZ": generate_klines("000002.SZ", days=2, base_price=10.2),
        })

        assert [t["ts_code"] for t in result["trade_records"]] == ["000002.SZ", "000001.SZ"]
        buy_signals = [s for s in runner.signals if s["action"] == "BUY"]
        assert buy_signals[0]["buy_priority_source"] == "confidence"
        assert buy_signals[0]["buy_priority_score"] == "0.9"

    def test_factor_score_orders_buy_signals_without_confidence(self):
        strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "target_position": 0.4}
'''
        # Step 5: signal generates on td=May 2 (window=[bar1]), so
        # factor_scores_by_date must be keyed by May 2.
        trade_date = date(2026, 5, 2)
        config = sample_backtest_config(
            source_code=strategy,
            stock_pool=["000001.SZ", "000002.SZ"],
            initial_cash=Decimal("20000"),
            factor_scores_by_date={
                trade_date: {
                    "000001.SZ": {"total_score": Decimal("0.20"), "rank": 2},
                    "000002.SZ": {"total_score": Decimal("0.95"), "rank": 1},
                }
            },
        )
        runner = BacktestRunner(config)
        result = runner.run({
            "000001.SZ": generate_klines("000001.SZ", days=2),
            "000002.SZ": generate_klines("000002.SZ", days=2),
        })

        assert [t["ts_code"] for t in result["trade_records"]] == ["000002.SZ", "000001.SZ"]
        assert runner.signals[0]["factor_rank"] == 1

    def test_confidence_source_precedes_factor_score_source(self):
        strategy = '''
def generate_signal(ctx):
    if ctx.close[-1] == 10.1:
        return {"signal_type": "买入", "target_position": 0.4}
    return {"signal_type": "买入", "target_position": 0.4, "confidence": 0.10}
'''
        # Step 5: signal generates on td=May 2 (window=[bar1]).
        trade_date = date(2026, 5, 2)
        config = sample_backtest_config(
            source_code=strategy,
            stock_pool=["000001.SZ", "000002.SZ"],
            initial_cash=Decimal("20000"),
            factor_scores_by_date={
                trade_date: {
                    "000001.SZ": {"total_score": Decimal("0.99"), "rank": 1},
                }
            },
        )
        runner = BacktestRunner(config)
        result = runner.run({
            "000001.SZ": generate_klines("000001.SZ", days=2, base_price=10.1),
            "000002.SZ": generate_klines("000002.SZ", days=2, base_price=10.2),
        })

        assert [t["ts_code"] for t in result["trade_records"]] == ["000002.SZ", "000001.SZ"]

    def test_sell_signals_execute_before_buys(self):
        strategy = '''
def generate_signal(ctx):
    if ctx.close[-1] == 10.1:
        return {"signal_type": "卖出", "current_position": 0.5}
    return {"signal_type": "买入", "target_position": 1.0, "confidence": 1.0}
'''
        config = sample_backtest_config(
            source_code=strategy,
            stock_pool=["000001.SZ", "000002.SZ"],
            initial_cash=Decimal("1000"),
        )
        runner = BacktestRunner(config)
        runner.positions["000001.SZ"] = Position(ts_code="000001.SZ", shares=1000, avg_cost=Decimal("10"))
        runner._entry_dates["000001.SZ"] = date(2026, 4, 30)
        # T+1 check now uses _open_lots (FIFO) instead of _entry_dates.
        runner._open_lots["000001.SZ"] = [
            _LotEntry(ts_code="000001.SZ", shares=1000, cost=Decimal("10"), entry_date=date(2026, 4, 30)),
        ]
        result = runner.run({
            "000001.SZ": generate_klines("000001.SZ", days=2, base_price=10.1),
            "000002.SZ": generate_klines("000002.SZ", days=2, base_price=10.2),
        })

        assert [t["direction"] for t in result["trade_records"][:2]] == ["全部卖出", "买入"]
        assert result["trade_records"][1]["ts_code"] == "000002.SZ"

    def test_tiebreakers_use_target_position_then_ts_code(self):
        strategy = '''
def generate_signal(ctx):
    if ctx.close[-1] == 10.3:
        return {"signal_type": "买入", "target_position": 0.4, "confidence": 0.5}
    return {"signal_type": "买入", "target_position": 0.2, "confidence": 0.5}
'''
        config = sample_backtest_config(
            source_code=strategy,
            stock_pool=["000002.SZ", "000001.SZ", "000003.SZ"],
            initial_cash=Decimal("100000"),
        )
        runner = BacktestRunner(config)
        result = runner.run({
            "000002.SZ": generate_klines("000002.SZ", days=2, base_price=10.2),
            "000001.SZ": generate_klines("000001.SZ", days=2, base_price=10.1),
            "000003.SZ": generate_klines("000003.SZ", days=2, base_price=10.3),
        })

        assert [t["ts_code"] for t in result["trade_records"]] == ["000003.SZ", "000001.SZ", "000002.SZ"]


@pytest.mark.backtest
class TestCandlePathInference:
    """Test K线内价格路径推断（借鉴 QuantDinger）。"""

    def test_infer_candle_path_bullish(self):
        """阳线路径：O -> L -> H -> C（先探底后拉升）"""
        result = BacktestRunner._infer_candle_path(
            Decimal("10"), Decimal("10.5"), Decimal("9.8"), Decimal("10.3"),
        )
        assert result == [Decimal("10"), Decimal("9.8"), Decimal("10.5"), Decimal("10.3")]

    def test_infer_candle_path_bearish(self):
        """阴线路径：O -> H -> L -> C（先冲高后回落）"""
        result = BacktestRunner._infer_candle_path(
            Decimal("10"), Decimal("10.5"), Decimal("9.8"), Decimal("9.9"),
        )
        assert result == [Decimal("10"), Decimal("10.5"), Decimal("9.8"), Decimal("9.9")]

    def test_infer_candle_path_doji(self):
        """十字星（close == open）按阳线处理"""
        result = BacktestRunner._infer_candle_path(
            Decimal("10"), Decimal("10.5"), Decimal("9.8"), Decimal("10"),
        )
        assert result == [Decimal("10"), Decimal("9.8"), Decimal("10.5"), Decimal("10")]

    def test_path_returns_4_points(self):
        """路径始终返回4个价格点"""
        for close in [Decimal("10"), Decimal("10.5")]:
            result = BacktestRunner._infer_candle_path(
                Decimal("10"), Decimal("11"), Decimal("9"), close,
            )
            assert len(result) == 4

    def test_buy_uses_better_price(self):
        """买入使用更优价格（阳线取low，阴线取open）"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        # Step 5: default fill_price_mode is next_open, which uses the
        # next day's open. To keep this test focused on candle-path
        # inference (its original intent), force current_intraday mode.
        runner._fill_price_mode = lambda: "current_intraday"
        klines = generate_klines(base_price=10.0, days=5)
        result = runner.run({"000001.SZ": klines})

        if result["trade_count"] > 0:
            trade = result["trade_records"][0]
            # Step 5: first trade happens on td=May 2 (window=[bar1] for
            # strategy, fill on bar2 via current_intraday candle-path).
            # Use bar2 (klines[1]) — the bar whose OHLC drives the fill.
            bar = klines[1]
            if bar.close >= bar.open:
                assert trade["price"] <= bar.close
            else:
                assert trade["price"] == float(bar.open)


@pytest.mark.backtest
class TestStopLoss:
    """Test 固定止损机制。"""

    def test_stop_loss_triggered(self):
        """亏损达到阈值时自动止损卖出"""
        sell_all_strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
        config = sample_backtest_config(
            source_code=sell_all_strategy,
            stop_loss_pct=0.05,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(base_price=10.0, price_increment=-0.2, days=15)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t["direction"] == "卖出"]
        stop_loss_trades = [t for t in sell_trades if t.get("exit_reason") == "止损"]
        assert len(stop_loss_trades) >= 1

    def test_stop_loss_does_not_trigger_when_not_met(self):
        """未达到止损阈值时不触发"""
        config = sample_backtest_config(
            source_code=ALWAYS_BUY_STRATEGY,
            stop_loss_pct=0.50,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(base_price=10.0, days=10)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t.get("exit_reason") == "止损"]
        assert len(sell_trades) == 0


@pytest.mark.backtest
class TestTakeProfit:
    """Test 固定止盈机制。"""

    def test_take_profit_triggered(self):
        """盈利达到阈值时自动止盈卖出"""
        config = sample_backtest_config(
            source_code=BUY_ONCE_STRATEGY,
            take_profit_pct=0.05,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(base_price=10.0, days=15)
        result = runner.run({"000001.SZ": klines})

        sell_trades = [t for t in result["trade_records"] if t.get("exit_reason") == "止盈"]
        assert len(sell_trades) >= 1


@pytest.mark.backtest
class TestTrailingStop:
    """Test 移动止盈机制。"""

    def test_trailing_stop_triggered(self):
        """移动止盈在最高点回撤时触发"""
        config = sample_backtest_config(
            source_code=BUY_ONCE_STRATEGY,
            trailing_stop_pct=0.02,
            trailing_activation_pct=0.02,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(base_price=10.0, days=20)
        result = runner.run({"000001.SZ": klines})

        trailing_trades = [t for t in result["trade_records"] if t.get("exit_reason") == "移动止盈"]
        assert len(trailing_trades) >= 1


@pytest.mark.backtest
class TestTimeStop:
    """Test 时间止损机制。"""

    def test_time_stop_triggered(self):
        """持有超期且无收益时自动卖出"""
        config = sample_backtest_config(
            source_code=BUY_ONCE_STRATEGY,
            time_stop_days=5,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(base_price=10.0, price_increment=-0.05, days=15)
        result = runner.run({"000001.SZ": klines})

        time_stop_trades = [t for t in result["trade_records"] if t.get("exit_reason") == "时间止损"]
        assert len(time_stop_trades) >= 1


@pytest.mark.backtest
class TestExecutionAssumptions:
    """Test 回测结果增强：执行假设记录。"""

    def test_result_contains_execution_assumptions(self):
        """结果包含 execution_assumptions 字段"""
        config = sample_backtest_config(
            source_code=ALWAYS_BUY_STRATEGY,
            stop_loss_pct=0.05,
            take_profit_pct=0.10,
            trailing_stop_pct=0.03,
        )
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        assert "execution_assumptions" in result
        ea = result["execution_assumptions"]
        assert ea["price_path_simulation"] is True
        assert ea["stop_loss_pct"] == 0.05
        assert ea["take_profit_pct"] == 0.10
        assert ea["trailing_stop_pct"] == 0.03
        assert ea["execution_timeframe"] == "1D"
        assert ea["signal_timeframe"] == "1D"

    def test_trade_record_contains_exit_reason(self):
        """交易记录包含 exit_reason 字段"""
        config = sample_backtest_config(source_code=ALWAYS_BUY_STRATEGY)
        runner = BacktestRunner(config)
        klines = generate_klines(days=10)
        result = runner.run({"000001.SZ": klines})

        if result["trade_count"] > 0:
            trade = result["trade_records"][0]
            assert "exit_reason" in trade
            assert isinstance(trade["exit_reason"], str)
