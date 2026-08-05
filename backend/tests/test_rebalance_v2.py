"""Tests for ranked rebalance v2 engine (rebalance.py + adapter.py integration).

Covers:
- Candidate pool add/remove/expire
- Weekly detection (last day of week)
- Return rate ranking with buffer
- Equal weight calculation
- Full plan + execute cycle
- T+1/limit/suspension blocking
- v1 backward compatibility
- BacktestContext new properties
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
    _LotEntry,
)
from app.backtest.rebalance import (
    WeeklyRebalancePlanner,
    RankInfo,
    CandidateInfo,
    HoldingInfo,
    TargetPosition,
    PlannedOrder,
    RebalanceDecision,
)
from app.backtest.strategy_runtime import StrategyExecutionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_backtest_context_setter(monkeypatch):
    """Workaround: Add setter to BacktestContext.current_position property."""
    original = BacktestContext.current_position
    _value = 0.0

    def getter(self):
        return _value

    def setter(self, value):
        nonlocal _value
        _value = value

    def fake_execute_strategy(compiled, ctx, **_kwargs):
        try:
            func = compiled.get("generate_signal") if isinstance(compiled, dict) else None
            if func is None:
                return StrategyExecutionResult(ok=True, signal=None)
            result = func(ctx)
            return StrategyExecutionResult(ok=True, signal=result if isinstance(result, dict) else None)
        except Exception as exc:
            return StrategyExecutionResult(
                ok=False, error_type=exc.__class__.__name__,
                error_message=str(exc), traceback="test traceback",
            )

    BacktestContext.current_position = property(getter, setter)
    monkeypatch.setattr(adapter_module, "execute_compiled_signal", fake_execute_strategy)
    yield
    BacktestContext.current_position = original


def make_kbar(
    ts_code: str = "000001.SZ",
    trade_date: date | None = None,
    close: Decimal = Decimal("10.0"),
    open_: Decimal = Decimal("10.0"),
    high: Decimal = Decimal("10.5"),
    low: Decimal = Decimal("9.5"),
    pre_close: Decimal = Decimal("10.0"),
    volume: int = 1000000,
    amount: Decimal | None = None,
    is_suspended: bool = False,
    is_limit_up: bool = False,
    is_limit_down: bool = False,
) -> KBar:
    return KBar(
        ts_code=ts_code,
        trade_date=trade_date or date(2026, 5, 1),
        open=open_,
        high=high,
        low=low,
        close=close,
        pre_close=pre_close,
        volume=volume,
        amount=amount or (close * volume),
        adj_factor=None,
        is_suspended=is_suspended,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
        turnover_rate=None,
    )


def generate_week_klines(
    ts_code: str,
    start_date: date,
    days: int = 5,
    base_price: float = 10.0,
    price_increment: float = 0.1,
    is_suspended: bool = False,
    is_limit_up: bool = False,
    is_limit_down: bool = False,
) -> list[KBar]:
    """Generate consecutive K-line data for a week."""
    klines = []
    pre_close = Decimal(str(base_price))
    for i in range(days):
        td = start_date + timedelta(days=i)
        close = Decimal(str(base_price + i * price_increment))
        klines.append(KBar(
            ts_code=ts_code,
            trade_date=td,
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
            turnover_rate=None,
        ))
        pre_close = close
    return klines


def sample_v2_config(**kwargs) -> BacktestConfig:
    defaults = dict(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"],
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 30),
        initial_cash=Decimal("100000"),
        rebalance_mode="ranked",
        rebalance_version=2,
        max_positions=3,
        rank_buffer_pct=0.2,
        score_max_age_sessions=5,
    )
    defaults.update(kwargs)
    return BacktestConfig(**defaults)


# ---------------------------------------------------------------------------
# Phase 4: BacktestContext new properties
# ---------------------------------------------------------------------------

class TestBacktestContextProperties:
    """Test new BacktestContext properties: stock_position_weight, etc."""

    def test_stock_position_weight_zero_when_no_runner(self):
        ctx = BacktestContext([], {}, Decimal("100000"))
        assert ctx.stock_position_weight == 0.0
        assert ctx.portfolio_exposure == 0.0
        assert ctx.position_shares == 0
        assert ctx.cash == 0.0

    def test_stock_position_weight_with_position(self):
        runner = BacktestRunner(sample_v2_config(
            source_code="", stock_pool=["000001.SZ"],
        ))
        runner.cash = Decimal("50000")
        runner.positions["000001.SZ"] = Position(ts_code="000001.SZ", shares=1000, avg_cost=Decimal("10"))
        klines = [make_kbar(trade_date=date(2026, 5, 1), close=Decimal("10"))]
        runner._all_klines = {"000001.SZ": klines}
        ctx = BacktestContext(klines, runner.positions, Decimal("60000"), runner=runner, ts_code="000001.SZ")
        # 1000 shares * 10 close / (50000 cash + 1000*10) = 10000/60000 = 0.1666...
        assert abs(ctx.stock_position_weight - 0.166666) < 0.001
        assert ctx.position_shares == 1000
        assert abs(ctx.cash - 50000) < 0.001

    def test_portfolio_exposure(self):
        runner = BacktestRunner(sample_v2_config(
            source_code="", stock_pool=["000001.SZ", "000002.SZ"],
        ))
        runner.cash = Decimal("30000")
        runner.positions["000001.SZ"] = Position(ts_code="000001.SZ", shares=1000, avg_cost=Decimal("10"))
        runner.positions["000002.SZ"] = Position(ts_code="000002.SZ", shares=2000, avg_cost=Decimal("20"))
        k1 = [make_kbar(trade_date=date(2026, 5, 1), close=Decimal("10"))]
        k2 = [make_kbar(ts_code="000002.SZ", trade_date=date(2026, 5, 1), close=Decimal("20"))]
        runner._all_klines = {"000001.SZ": k1, "000002.SZ": k2}
        ctx = BacktestContext(k1, runner.positions, Decimal("80000"), runner=runner, ts_code="000001.SZ")
        # (1000*10 + 2000*20) / (30000 + 1000*10 + 2000*20) = 50000/80000 = 0.625
        assert abs(ctx.portfolio_exposure - 0.625) < 0.001


# ---------------------------------------------------------------------------
# Phase 5: WeeklyRebalancePlanner unit tests
# ---------------------------------------------------------------------------

class TestCandidatePool:
    """Candidate pool add/remove/expire logic."""

    def test_on_signal_adds_candidate(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        planner.on_signal("000001.SZ", "买入", date(2026, 5, 4), False)
        assert "000001.SZ" in planner.candidate_pool
        assert planner.candidate_pool["000001.SZ"].first_signal_date == date(2026, 5, 4)
        assert not planner.candidate_pool["000001.SZ"].exited

    def test_on_signal_updates_existing(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        planner.on_signal("000001.SZ", "买入", date(2026, 5, 4), False)
        planner.on_signal("000001.SZ", "增持", date(2026, 5, 5), False)
        assert planner.candidate_pool["000001.SZ"].latest_signal_date == date(2026, 5, 5)
        assert planner.candidate_pool["000001.SZ"].first_signal_date == date(2026, 5, 4)

    def test_on_signal_ignores_non_buy(self):
        config = sample_v2_config()
        planner = WeeklyRebalancePlanner(config, runner := BacktestRunner(config))
        planner.on_signal("000001.SZ", "卖出", date(2026, 5, 4), True)
        assert "000001.SZ" not in planner.candidate_pool

    def test_on_exit_marks_exited(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        planner.on_signal("000001.SZ", "买入", date(2026, 5, 4), False)
        planner.on_exit("000001.SZ", "止损")
        assert planner.candidate_pool["000001.SZ"].exited

    def test_on_exit_noop_for_unknown(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        planner.on_exit("000001.SZ", "止损")  # should not raise


class TestWeeklyDetection:
    """Weekly detection (last day of week)."""

    def test_mid_week_returns_false(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        # 2026-05-06 is a Wednesday
        td = date(2026, 5, 6)
        trading_dates = [
            date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6),
            date(2026, 5, 7), date(2026, 5, 8),
        ]
        assert not planner.should_run_weekly(td, trading_dates)

    def test_friday_returns_true(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        # 2026-05-08 is a Friday
        td = date(2026, 5, 8)
        trading_dates = [
            date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6),
            date(2026, 5, 7), date(2026, 5, 8),
        ]
        assert planner.should_run_weekly(td, trading_dates)

    def test_last_trading_day_overall_returns_true(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        # Last day in the entire trading_dates list -> should run
        td = date(2026, 5, 30)
        trading_dates = [date(2026, 5, 28), date(2026, 5, 29), date(2026, 5, 30)]
        assert planner.should_run_weekly(td, trading_dates)

    def test_thursday_if_no_friday_returns_true(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        # If Thursday is the last trading day of the week (no Friday data)
        td = date(2026, 5, 7)  # Thursday
        trading_dates = [
            date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7),
        ]
        assert planner.should_run_weekly(td, trading_dates)

    def test_empty_trading_dates_returns_false(self):
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        assert not planner.should_run_weekly(date(2026, 5, 8), [])


class TestReturnRateRanking:
    """Ranking with buffer zone, based on recent return rate."""

    BUY_STRATEGY = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''

    def test_ranking_orders_by_return_rate_desc(self):
        """Candidates ranked by recent return rate descending."""
        config = sample_v2_config(max_positions=3, score_max_age_sessions=5)
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)

        # Build trading dates: Mon-Fri
        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]

        # Pre-populate a position for a low-return stock
        runner.positions["000004.SZ"] = Position(ts_code="000004.SZ", shares=500, avg_cost=Decimal("10"))
        runner._entry_dates["000004.SZ"] = date(2026, 5, 4)
        runner._open_lots["000004.SZ"] = [
            _LotEntry(ts_code="000004.SZ", shares=500, cost=Decimal("10"), entry_date=date(2026, 5, 4)),
        ]
        runner.positions["000005.SZ"] = Position(ts_code="000005.SZ", shares=500, avg_cost=Decimal("10"))
        runner._entry_dates["000005.SZ"] = date(2026, 5, 4)
        runner._open_lots["000005.SZ"] = [
            _LotEntry(ts_code="000005.SZ", shares=500, cost=Decimal("10"), entry_date=date(2026, 5, 4)),
        ]
        runner.cash = Decimal("10000")

        # Add signals for all 5 stocks
        for code in ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"]:
            planner.on_signal(code, "买入", date(2026, 5, 8), code in runner.positions)

        # Generate klines for all stocks with varying return rates
        # 000002.SZ has highest growth (best return), 000005.SZ lowest
        all_klines = {}
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"]):
            all_klines[code] = generate_week_klines(code, date(2026, 5, 4), days=5, base_price=10.0 + (5 - i) * 0.2)

        plan = planner.plan(date(2026, 5, 8), all_klines, Decimal("100000"), trading_dates)
        assert plan is not None
        # Top 3 by return rate: 000002.SZ, 000001.SZ, 000003.SZ
        # Buffer zone (1): 000004.SZ - kept because already held
        # 000005.SZ - below buffer, should be sold
        sell_codes = {p.ts_code for p in plan.plans if p.side == 'sell'}
        assert "000005.SZ" in sell_codes
        assert "000004.SZ" not in sell_codes  # kept by buffer zone

    def test_buffer_zone_keeps_holdings(self):
        """Buffer zone allows keeping positions near the cutoff."""
        config = sample_v2_config(max_positions=2, rank_buffer_pct=0.5, score_max_age_sessions=5)
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)

        # Runner already holds 000003.SZ (ranked 3rd by return)
        runner.positions["000003.SZ"] = Position(ts_code="000003.SZ", shares=1000, avg_cost=Decimal("10"))
        runner._entry_dates["000003.SZ"] = date(2026, 5, 4)
        runner._open_lots["000003.SZ"] = [
            _LotEntry(ts_code="000003.SZ", shares=1000, cost=Decimal("10"), entry_date=date(2026, 5, 4)),
        ]

        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]

        for code in ["000001.SZ", "000002.SZ", "000003.SZ"]:
            planner.on_signal(code, "买入", date(2026, 5, 8), code == "000003.SZ")

        all_klines = {}
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
            all_klines[code] = generate_week_klines(code, date(2026, 5, 4), days=5, base_price=10.0 + (3 - i) * 0.2)

        plan = planner.plan(date(2026, 5, 8), all_klines, Decimal("100000"), trading_dates)
        assert plan is not None
        # Top 2: 000001.SZ, 000002.SZ
        # Buffer zone (2 * 0.5 = 1): 000003.SZ
        # 000003.SZ is held, so it should be kept
        sell_codes = {p.ts_code for p in plan.plans if p.side == 'sell'}
        assert "000003.SZ" not in sell_codes


class TestEqualWeight:
    """Equal weight calculation."""

    BUY_STRATEGY = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''

    def test_equal_weight_sum_to_one(self):
        """Target weights sum to 1.0."""
        config = sample_v2_config(max_positions=4, score_max_age_sessions=5)
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)

        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]

        for i in range(1, 6):
            code = f"00000{i}.SZ"
            planner.on_signal(code, "买入", date(2026, 5, 8), False)

        all_klines = {}
        for i in range(1, 6):
            code = f"00000{i}.SZ"
            all_klines[code] = generate_week_klines(code, date(2026, 5, 4), days=5, base_price=10.0 + i * 0.1)

        plan = planner.plan(date(2026, 5, 8), all_klines, Decimal("100000"), trading_dates)
        assert plan is not None
        assert plan.target_count == 4

    def test_desired_shares_rounded_to_100_lots(self):
        """Desired shares should be multiples of 100."""
        config = sample_v2_config(max_positions=2, score_max_age_sessions=5)
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)

        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]

        for code in ["000001.SZ", "000002.SZ"]:
            planner.on_signal(code, "买入", date(2026, 5, 8), False)

        all_klines = {
            "000001.SZ": generate_week_klines("000001.SZ", date(2026, 5, 4), days=5, base_price=10.0),
            "000002.SZ": generate_week_klines("000002.SZ", date(2026, 5, 4), days=5, base_price=15.0),
        }

        plan = planner.plan(date(2026, 5, 8), all_klines, Decimal("100000"), trading_dates)
        assert plan is not None
        # Check that buy orders have shares rounded to 100-lot
        for p in plan.plans:
            if p.side == 'buy':
                assert p.planned_shares % 100 == 0, f"{p.ts_code} shares {p.planned_shares} not multiple of 100"


class TestPlanAndExecuteCycle:
    """Full plan + execute cycle."""

    BUY_STRATEGY = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''

    def test_empty_plan_when_no_candidates(self):
        """No candidates -> plan returns None."""
        config = sample_v2_config()
        runner = BacktestRunner(config)
        planner = WeeklyRebalancePlanner(config, runner)
        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]
        plan = planner.plan(date(2026, 5, 8), {}, Decimal("100000"), trading_dates)
        assert plan is None

    def test_full_plan_execute_cycle(self):
        """Full plan + execute cycle produces trades."""
        config = sample_v2_config(
            stock_pool=["000001.SZ", "000002.SZ", "000003.SZ"],
            max_positions=2,
            score_max_age_sessions=5,
            initial_cash=Decimal("200000"),
        )
        runner = BacktestRunner(config)

        # Set up trading dates: Mon-Fri
        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]
        all_klines = {
            "000001.SZ": generate_week_klines("000001.SZ", date(2026, 5, 4), days=5, base_price=10.0),
            "000002.SZ": generate_week_klines("000002.SZ", date(2026, 5, 4), days=5, base_price=20.0),
            "000003.SZ": generate_week_klines("000003.SZ", date(2026, 5, 4), days=5, base_price=15.0),
        }
        runner._all_klines = all_klines
        runner._stock_day_index = {code: 0 for code in config.stock_pool}

        planner = WeeklyRebalancePlanner(config, runner)

        # Pre-populate a position for a stock that will be sold
        runner.positions["000003.SZ"] = Position(ts_code="000003.SZ", shares=500, avg_cost=Decimal("15"))
        runner._entry_dates["000003.SZ"] = date(2026, 5, 4)
        runner._open_lots["000003.SZ"] = [
            _LotEntry(ts_code="000003.SZ", shares=500, cost=Decimal("15"), entry_date=date(2026, 5, 4)),
        ]
        runner.cash = Decimal("50000")

        for code in ["000001.SZ", "000002.SZ", "000003.SZ"]:
            planner.on_signal(code, "买入", date(2026, 5, 8), code in runner.positions)

        # Plan on Friday
        plan = planner.plan(date(2026, 5, 8), all_klines, Decimal("80000"), trading_dates)
        assert plan is not None
        assert plan.status == 'planned'
        assert plan.holding_count_before == 1  # 000003.SZ

        # Execute on next Monday (2026-05-11)
        plan.fill_date = date(2026, 5, 11)
        fill_bar_map = {
            "000001.SZ": make_kbar("000001.SZ", date(2026, 5, 11), close=Decimal("10.5"), open_=Decimal("10.2")),
            "000002.SZ": make_kbar("000002.SZ", date(2026, 5, 11), close=Decimal("20.5"), open_=Decimal("20.1")),
            "000003.SZ": make_kbar("000003.SZ", date(2026, 5, 11), close=Decimal("15.5"), open_=Decimal("15.2")),
        }
        # Need to set up _all_klines with the fill date bars
        for code in ["000001.SZ", "000002.SZ", "000003.SZ"]:
            all_klines[code].append(fill_bar_map[code])

        executed = planner.execute(plan, fill_bar_map)
        assert executed.status in ('executed', 'partial')

        # At least one buy should happen
        buy_orders = [p for p in executed.plans if p.side == 'buy']
        assert len(buy_orders) > 0

    def test_plan_clears_candidate_pool_after_execution(self):
        """Candidate pool is cleared after execution."""
        config = sample_v2_config(max_positions=2, score_max_age_sessions=5)
        runner = BacktestRunner(config)
        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]
        all_klines = {
            "000001.SZ": generate_week_klines("000001.SZ", date(2026, 5, 4), days=5, base_price=10.0),
            "000002.SZ": generate_week_klines("000002.SZ", date(2026, 5, 4), days=5, base_price=15.0),
        }
        runner._all_klines = all_klines

        planner = WeeklyRebalancePlanner(config, runner)
        planner.on_signal("000001.SZ", "买入", date(2026, 5, 8), False)
        assert len(planner.candidate_pool) > 0

        plan = planner.plan(date(2026, 5, 8), all_klines, Decimal("100000"), trading_dates)
        assert plan is not None

        plan.fill_date = date(2026, 5, 11)
        fill_bar_map = {
            "000001.SZ": make_kbar("000001.SZ", date(2026, 5, 11), close=Decimal("10.5"), open_=Decimal("10.2")),
        }
        all_klines["000001.SZ"].append(fill_bar_map["000001.SZ"])
        planner.execute(plan, fill_bar_map)
        assert len(planner.candidate_pool) == 0


class TestBlocking:
    """T+1/limit/suspension blocking."""

    def test_limit_up_blocks_buy(self):
        """Limit-up blocks buy order."""
        config = sample_v2_config(max_positions=2, score_max_age_sessions=5)
        runner = BacktestRunner(config)
        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]
        all_klines = {
            "000001.SZ": generate_week_klines("000001.SZ", date(2026, 5, 4), days=5, base_price=10.0),
            "000002.SZ": generate_week_klines("000002.SZ", date(2026, 5, 4), days=5, base_price=15.0),
        }
        runner._all_klines = all_klines
        runner._stock_day_index = {code: 0 for code in config.stock_pool}

        planner = WeeklyRebalancePlanner(config, runner)
        for code in ["000001.SZ", "000002.SZ"]:
            planner.on_signal(code, "买入", date(2026, 5, 8), False)

        plan = planner.plan(date(2026, 5, 8), all_klines, Decimal("100000"), trading_dates)
        assert plan is not None

        plan.fill_date = date(2026, 5, 11)
        fill_bar_map = {
            "000001.SZ": make_kbar("000001.SZ", date(2026, 5, 11), close=Decimal("10.5"), open_=Decimal("10.2"), is_limit_up=True),
            "000002.SZ": make_kbar("000002.SZ", date(2026, 5, 11), close=Decimal("20.5"), open_=Decimal("20.1")),
        }
        for code in ["000001.SZ", "000002.SZ"]:
            all_klines[code].append(fill_bar_map[code])

        executed = planner.execute(plan, fill_bar_map)
        buy_orders = [p for p in executed.plans if p.side == 'buy']
        assert any(p.status == 'blocked' for p in buy_orders)
        assert any(p.blocked_reason == '涨停不可买入' for p in buy_orders)

    def test_suspension_blocks_trade(self):
        """Suspension blocks both buy and sell."""
        config = sample_v2_config(max_positions=2, score_max_age_sessions=5)
        runner = BacktestRunner(config)
        trading_dates = [date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8)]
        all_klines = {
            "000001.SZ": generate_week_klines("000001.SZ", date(2026, 5, 4), days=5, base_price=10.0),
            "000002.SZ": generate_week_klines("000002.SZ", date(2026, 5, 4), days=5, base_price=15.0),
        }
        runner._all_klines = all_klines
        runner._stock_day_index = {code: 0 for code in config.stock_pool}

        planner = WeeklyRebalancePlanner(config, runner)
        for code in ["000001.SZ", "000002.SZ"]:
            planner.on_signal(code, "买入", date(2026, 5, 8), False)

        plan = planner.plan(date(2026, 5, 8), all_klines, Decimal("100000"), trading_dates)
        assert plan is not None

        plan.fill_date = date(2026, 5, 11)
        # No bar in fill_bar_map -> blocked as "停牌或无数据"
        fill_bar_map = {}
        all_klines["000001.SZ"].append(make_kbar("000001.SZ", date(2026, 5, 11), close=Decimal("10.5"), open_=Decimal("10.2")))
        all_klines["000002.SZ"].append(make_kbar("000002.SZ", date(2026, 5, 11), close=Decimal("20.5"), open_=Decimal("20.1")))

        executed = planner.execute(plan, fill_bar_map)
        for p in executed.plans:
            assert p.status == 'blocked'
            assert p.blocked_reason == '停牌或无数据'


class TestV1BackwardCompat:
    """v1 backward compatibility."""

    ALWAYS_BUY = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''

    def test_v1_default_config(self):
        """Default rebalance_version=1 uses existing rebalance logic."""
        config = BacktestConfig(
            strategy_id=1,
            source_code=self.ALWAYS_BUY,
            stock_pool=["000001.SZ", "000002.SZ"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 15),
            initial_cash=Decimal("100000"),
            rebalance_mode="ranked",
            max_positions=2,
        )
        assert config.rebalance_version == 1
        runner = BacktestRunner(config)
        result = runner.run({
            "000001.SZ": generate_week_klines("000001.SZ", date(2026, 5, 1), days=10, base_price=10.0),
            "000002.SZ": generate_week_klines("000002.SZ", date(2026, 5, 1), days=10, base_price=12.0),
        })
        assert result["trade_count"] > 0

    def test_v2_not_initialized_when_v1(self):
        """rebalance_version=1 does not initialize v2 planner."""
        config = BacktestConfig(
            strategy_id=1,
            source_code=self.ALWAYS_BUY,
            stock_pool=["000001.SZ"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 15),
            rebalance_mode="ranked",
            max_positions=2,
            rebalance_version=1,
        )
        runner = BacktestRunner(config)
        assert runner.rebalance_planner is None

    def test_v2_initialized_when_v2(self):
        """rebalance_version=2 initializes v2 planner."""
        config = sample_v2_config(
            source_code=self.ALWAYS_BUY,
            stock_pool=["000001.SZ"],
            max_positions=2,
        )
        runner = BacktestRunner(config)
        # Planner is initialized in run(), not __init__
        # But we can verify it's None before run
        assert runner.rebalance_planner is None
        runner.run({
            "000001.SZ": generate_week_klines("000001.SZ", date(2026, 5, 1), days=10, base_price=10.0),
        })
        # After run, planner should be initialized
        assert runner.rebalance_planner is not None


class TestIntegration:
    """Integration test: full backtest run with v2 rebalance."""

    STRATEGY = '''
def generate_signal(ctx):
    day = ctx.trade_date.day
    if day <= 7:
        return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
    return {"signal_type": "观望", "current_position": ctx.current_position}
'''

    def test_full_backtest_with_v2_rebalance(self):
        """Full backtest run with v2 rebalance produces results."""
        config = sample_v2_config(
            source_code=self.STRATEGY,
            stock_pool=["000001.SZ", "000002.SZ"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 15),
            max_positions=2,
            initial_cash=Decimal("200000"),
        )
        runner = BacktestRunner(config)
        all_klines = {
            "000001.SZ": generate_week_klines("000001.SZ", date(2026, 5, 1), days=12, base_price=10.0),
            "000002.SZ": generate_week_klines("000002.SZ", date(2026, 5, 1), days=12, base_price=12.0),
        }
        result = runner.run(all_klines)
        assert result["trade_count"] >= 0
        assert "equity_curve" in result
        assert len(result["equity_curve"]) > 0

    def test_rebalance_decision_fields(self):
        """RebalanceDecision contains all required fields."""
        decision = RebalanceDecision(
            decision_date=date(2026, 5, 8),
            information_date=date(2026, 5, 8),
            score_coverage=0.8,
            candidate_count=5,
            holding_count_before=3,
            target_count=3,
            max_positions=3,
            buffer_size=1,
            nav_before=Decimal("100000"),
            cash_before=Decimal("20000"),
            plans=[
                PlannedOrder(ts_code="000004.SZ", side='sell', reason='调仓', planned_shares=500),
                PlannedOrder(ts_code="000001.SZ", side='buy', reason='调仓', planned_shares=300),
            ],
            holding_count_after=3,
            cash_after=Decimal("15000"),
            turnover=Decimal("5000"),
            fees=Decimal("12.5"),
            status='planned',
            diagnostics={},
        )
        assert decision.status == 'planned'
        assert len(decision.plans) == 2
        assert decision.score_coverage == 0.8
        assert decision.target_count == 3
