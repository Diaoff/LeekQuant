"""Tests for P1-B: lookahead bias fix in backtest engine.

Verifies that:
1. Strategy context (window) EXCLUDES the current day's bar — strategy
   generates signals "as of start of td" using only data through td-1.
   Previously the strategy could see td's close (future information) and
   then "fill" at td's intraday prices — a half-day to full-day lookahead.
2. Fill price uses next day's open (BACKTEST_FILL_PRICE_MODE=next_open)
   when fill_bar is available, eliminating lookahead in execution price.
3. Falls back to current_intraday candle-path inference when fill_bar
   is unavailable (e.g., last trading day).
4. Supports current_close and current_intraday fill modes.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.backtest import adapter as adapter_module
from app.backtest.adapter import (
    BacktestConfig,
    BacktestContext,
    BacktestRunner,
    KBar,
)
from app.backtest.signals import SignalOutput
from app.backtest.strategy_runtime import StrategyExecutionResult
from app.libs import MyTT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_backtest_context_position_setter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workaround: BacktestContext.current_position is read-only.

    _exec_strategy tries to set ctx.current_position but the property only
    defines a getter. This monkey-patch installs a setter for the duration
    of each test (same pattern as test_adapter.py).
    """
    original = BacktestContext.current_position
    _position_value = 0.0

    def getter(self: BacktestContext) -> float:
        return _position_value

    def setter(self: BacktestContext, value: float) -> None:
        nonlocal _position_value
        _position_value = value

    BacktestContext.current_position = property(getter, setter)
    yield
    BacktestContext.current_position = original


def _make_bar(
    trade_date: date,
    *,
    open_: Decimal | float = 10.0,
    high: Decimal | float | None = None,
    low: Decimal | float | None = None,
    close: Decimal | float | None = None,
    pre_close: Decimal | float = 10.0,
    adj_factor: Decimal | None = None,
    is_suspended: bool = False,
    is_limit_up: bool = False,
    is_limit_down: bool = False,
    ts_code: str = "000001.SZ",
) -> KBar:
    """Build a single KBar with sensible defaults."""
    open_d = Decimal(str(open_))
    close_d = Decimal(str(close)) if close is not None else open_d
    high_d = Decimal(str(high)) if high is not None else open_d + Decimal("0.2")
    low_d = Decimal(str(low)) if low is not None else open_d - Decimal("0.2")
    return KBar(
        ts_code=ts_code,
        trade_date=trade_date,
        open=open_d,
        high=high_d,
        low=low_d,
        close=close_d,
        pre_close=Decimal(str(pre_close)),
        volume=1_000_000,
        amount=open_d * 1_000_000,
        adj_factor=adj_factor,
        is_suspended=is_suspended,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
        turnover_rate=None,
    )


def _make_config(
    *,
    source_code: str = "",
    stock_pool: list[str] | None = None,
    start_date: date = date(2026, 5, 1),
    end_date: date = date(2026, 5, 15),
    initial_cash: Decimal = Decimal("100000"),
    slippage_pct: float = 0.001,
    **kwargs: Any,
) -> BacktestConfig:
    """Build BacktestConfig with defaults."""
    return BacktestConfig(
        strategy_id=1,
        source_code=source_code,
        stock_pool=stock_pool or ["000001.SZ"],
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        slippage_pct=slippage_pct,
        **kwargs,
    )


def _make_runner(
    *,
    fill_price_mode: str = "next_open",
    adjust_mode: str = "qfq",
    monkeypatch: pytest.MonkeyPatch | None = None,
    **config_kwargs: Any,
) -> BacktestRunner:
    """Build a BacktestRunner with patched fill_price_mode and adjust_mode.

    The lazy `_fill_price_mode()` / `_adjust_mode()` helpers read from
    app.core.config.get_settings() at runtime. We patch those helpers
    directly to avoid depending on env vars or settings cache state.
    """
    runner = BacktestRunner(_make_config(**config_kwargs))
    if monkeypatch is not None:
        monkeypatch.setattr(runner, "_fill_price_mode", lambda: fill_price_mode)
        monkeypatch.setattr(runner, "_adjust_mode", lambda: adjust_mode)
    return runner


# ---------------------------------------------------------------------------
# Test 1: strategy window excludes current day's bar
# ---------------------------------------------------------------------------


@pytest.mark.backtest
class TestSignalUsesPreviousDayWindow:
    """Strategy must NOT see the current day's bar in ctx.close/open/etc.

    The fix: `window = bars_through_td[-lookback:-1]` (excludes td's bar)
    instead of the old `bars_through_td[-lookback:]` (includes td's bar).

    This is the core lookahead-bias fix: the strategy decides "as of
    start of td" using only data through td-1.
    """

    def test_signal_uses_previous_day_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On td=2026-05-03, ctx.close[-1] must equal 2026-05-02's close.

        Setup: 3 bars (May 1, 2, 3) with distinct closes 10.0, 11.0, 12.0.
        Strategy captures ctx.close[-1] in a closure variable.

        Expected:
        - td=May 1: window empty → skipped (no signal)
        - td=May 2: window = [bar1] → strategy sees close=10.0 (May 1)
        - td=May 3: window = [bar1, bar2] → strategy sees close=11.0 (May 2)

        NOT 12.0 (May 3's close) — that would be lookahead.
        """
        captured_last_closes: list[float] = []

        capturing_strategy = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    # Stash ctx.close[-1] into a module-level list via __capture__
    __capture__(ctx.close[-1])
    return None
'''

        def fake_execute_signal(
            compiled: dict[str, Any], ctx: BacktestContext, **_kwargs: Any
        ) -> StrategyExecutionResult:
            if len(ctx.close) > 0:
                captured_last_closes.append(ctx.close[-1])
            func = compiled.get("generate_signal")
            if func is None:
                return StrategyExecutionResult(ok=True, signal=None)
            try:
                result = func(ctx)
                return StrategyExecutionResult(
                    ok=True, signal=result if isinstance(result, dict) else None
                )
            except Exception as exc:
                return StrategyExecutionResult(
                    ok=False,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    traceback="test traceback",
                )

        monkeypatch.setattr(adapter_module, "execute_compiled_signal", fake_execute_signal)

        runner = _make_runner(
            source_code=capturing_strategy,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 3),
            monkeypatch=monkeypatch,
        )
        klines = [
            _make_bar(date(2026, 5, 1), open_=10.0, close=10.0),
            _make_bar(date(2026, 5, 2), open_=11.0, close=11.0),
            _make_bar(date(2026, 5, 3), open_=12.0, close=12.0),
        ]
        runner.run({"000001.SZ": klines})

        # Day 1 (May 1) is skipped because window[:-1] is empty.
        # Day 2 (May 2): strategy sees ctx.close[-1] = bar1.close = 10.0
        # Day 3 (May 3): strategy sees ctx.close[-1] = bar2.close = 11.0
        # Must NOT see 12.0 (bar3 = today's close) — that's lookahead.
        assert captured_last_closes == [10.0, 11.0], (
            f"Strategy should see closes from previous day only (10.0, 11.0), "
            f"got {captured_last_closes}. If 12.0 appears, the lookahead bug is back."
        )

    def test_first_day_skipped_when_window_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On the first trading day, window[:-1] is empty → skip (no signal).

        This is correct behavior: with only 1 bar through td, excluding
        td's bar leaves an empty window. The strategy cannot decide without
        any historical context.
        """
        call_count = 0

        def fake_execute_signal(
            compiled: dict[str, Any], ctx: BacktestContext, **_kwargs: Any
        ) -> StrategyExecutionResult:
            nonlocal call_count
            call_count += 1
            return StrategyExecutionResult(ok=True, signal=None)

        monkeypatch.setattr(adapter_module, "execute_compiled_signal", fake_execute_signal)

        runner = _make_runner(
            source_code="def generate_signal(ctx): return None",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            monkeypatch=monkeypatch,
        )
        runner.run({"000001.SZ": [_make_bar(date(2026, 5, 1), open_=10.0, close=10.0)]})

        # Strategy must NOT be called on day 1 (empty window)
        assert call_count == 0, (
            "Strategy should not be called on the first day when window[:-1] is empty"
        )


# ---------------------------------------------------------------------------
# Test 2: fill price uses next day's open
# ---------------------------------------------------------------------------


@pytest.mark.backtest
class TestFillPriceUsesNextOpen:
    """When BACKTEST_FILL_PRICE_MODE=next_open (default), buy/sell fill
    price must equal next day's open (after slippage), and trade_date
    must be next day's date."""

    def test_fill_price_uses_next_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BUY on td=May 2 fills at td+1 (May 3) open price.

        Setup:
        - Strategy: always buy on any day with window data
        - K-lines: May 1 (close=10), May 2 (close=11), May 3 (open=11.5)
        - Slippage 0.1% (default)

        Expected:
        - On td=May 2, strategy sees [bar1.close=10] → BUY
        - fill_bar = bar3 (May 3's bar)
        - fill_price = bar3.open * (1 + 0.001) = 11.5 * 1.001 = 11.5115
        - trade_date = bar3.trade_date = May 3
        """
        buy_strategy = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
        runner = _make_runner(
            source_code=buy_strategy,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 3),
            initial_cash=Decimal("100000"),
            monkeypatch=monkeypatch,
            fill_price_mode="next_open",
        )
        klines = [
            _make_bar(date(2026, 5, 1), open_=10.0, close=10.0),
            _make_bar(date(2026, 5, 2), open_=11.0, close=11.0),
            _make_bar(date(2026, 5, 3), open_=11.5, close=12.0),
        ]
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] >= 1, "Should have at least one buy trade"
        first_trade = result["trade_records"][0]
        # Fill price = next day's open * (1 + slippage)
        expected_price = float(Decimal("11.5") * Decimal("1.001"))
        assert first_trade["price"] == pytest.approx(expected_price, rel=1e-6), (
            f"Fill price should be next_open * (1+slippage) = {expected_price}, "
            f"got {first_trade['price']}"
        )
        # Trade date = next day (fill_bar.trade_date)
        assert first_trade["trade_date"] == "2026-05-03", (
            f"Trade date should be next day (2026-05-03), got {first_trade['trade_date']}"
        )

    def test_trade_date_uses_fill_bar_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TradeRecord.trade_date must equal fill_bar.trade_date in next_open mode.

        This is critical for T+1 lot accounting: a lot bought "today" but
        filled "tomorrow" must be sellable starting from td+2, not td+1.
        """
        buy_strategy = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
        runner = _make_runner(
            source_code=buy_strategy,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 5),
            monkeypatch=monkeypatch,
            fill_price_mode="next_open",
        )
        klines = [
            _make_bar(date(2026, 5, 1), open_=10.0, close=10.0),
            _make_bar(date(2026, 5, 2), open_=10.5, close=10.5),
            _make_bar(date(2026, 5, 3), open_=11.0, close=11.0),
            _make_bar(date(2026, 5, 4), open_=11.5, close=11.5),
            _make_bar(date(2026, 5, 5), open_=12.0, close=12.0),
        ]
        result = runner.run({"000001.SZ": klines})

        # First buy: signal on May 2 (window has bar1), fill on May 3
        trades = result["trade_records"]
        assert len(trades) >= 1
        first_buy = trades[0]
        assert first_buy["trade_date"] == "2026-05-03", (
            f"First buy should fill on May 3 (next day's open), "
            f"got {first_buy['trade_date']}"
        )

    def test_fill_bar_none_falls_back_to_intraday(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When fill_bar is None (last trading day), fill mode falls back
        to current_intraday candle-path inference.

        Setup: only 2 days. On td=May 2, fill_bar=None (no May 3 bar).
        Expected: fill_price derived from bar2's OHLC via candle-path,
        trade_date = bar2.trade_date (not next day).
        """
        buy_strategy = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
        runner = _make_runner(
            source_code=buy_strategy,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 2),
            monkeypatch=monkeypatch,
            fill_price_mode="next_open",
        )
        # Bar 2: bullish candle (close > open) → fill at low per candle-path
        klines = [
            _make_bar(date(2026, 5, 1), open_=10.0, close=10.0),
            _make_bar(date(2026, 5, 2), open_=11.0, high=11.5, low=10.8, close=11.2),
        ]
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] >= 1
        trade = result["trade_records"][0]
        # Bullish candle: fill at low (price_path[1]) = 10.8 * (1 + 0.001)
        expected_price = float(Decimal("10.8") * Decimal("1.001"))
        assert trade["price"] == pytest.approx(expected_price, rel=1e-6), (
            f"fill_bar=None fallback should use candle-path (low for bullish) = {expected_price}, "
            f"got {trade['price']}"
        )
        # trade_date should be td (no fill_bar to shift it)
        assert trade["trade_date"] == "2026-05-02"


# ---------------------------------------------------------------------------
# Test 3: current_close fill mode
# ---------------------------------------------------------------------------


@pytest.mark.backtest
class TestFillPriceModeCurrentClose:
    """When BACKTEST_FILL_PRICE_MODE=current_close, fill at signal day's close."""

    def test_fill_price_uses_current_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BUY fills at td's close (adj_close) * (1 + slippage).

        trade_date = bar.trade_date (signal day, NOT next day).
        """
        buy_strategy = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
        runner = _make_runner(
            source_code=buy_strategy,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 3),
            monkeypatch=monkeypatch,
            fill_price_mode="current_close",
        )
        klines = [
            _make_bar(date(2026, 5, 1), open_=10.0, close=10.0),
            _make_bar(date(2026, 5, 2), open_=11.0, close=11.5),  # signal day's close
            _make_bar(date(2026, 5, 3), open_=12.0, close=12.5),
        ]
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] >= 1
        first_trade = result["trade_records"][0]
        # current_close: fill at td's close (bar2.close=11.5) * (1 + slippage)
        expected_price = float(Decimal("11.5") * Decimal("1.001"))
        assert first_trade["price"] == pytest.approx(expected_price, rel=1e-6), (
            f"current_close mode: fill should be bar.close * (1+slippage) = {expected_price}, "
            f"got {first_trade['price']}"
        )
        # trade_date = signal day (bar.trade_date), NOT fill_bar.trade_date
        assert first_trade["trade_date"] == "2026-05-02", (
            f"current_close mode: trade_date should be signal day (2026-05-02), "
            f"got {first_trade['trade_date']}"
        )


# ---------------------------------------------------------------------------
# Test 4: current_intraday (legacy) fill mode
# ---------------------------------------------------------------------------


@pytest.mark.backtest
class TestFillPriceModeCurrentIntraday:
    """When BACKTEST_FILL_PRICE_MODE=current_intraday (legacy), use
    candle-path inference on signal day's OHLC."""

    def test_fill_price_uses_candle_path_for_bullish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bullish candle (close >= open): BUY fills at low (price_path[1])."""
        buy_strategy = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
        runner = _make_runner(
            source_code=buy_strategy,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 3),
            monkeypatch=monkeypatch,
            fill_price_mode="current_intraday",
        )
        # Bar 2 bullish: open=11.0, close=11.5 (close > open)
        klines = [
            _make_bar(date(2026, 5, 1), open_=10.0, close=10.0),
            _make_bar(date(2026, 5, 2), open_=11.0, high=11.6, low=10.9, close=11.5),
            _make_bar(date(2026, 5, 3), open_=12.0, close=12.5),
        ]
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] >= 1
        trade = result["trade_records"][0]
        # Bullish: fill at low = 10.9 * (1 + 0.001)
        expected_price = float(Decimal("10.9") * Decimal("1.001"))
        assert trade["price"] == pytest.approx(expected_price, rel=1e-6), (
            f"current_intraday bullish: fill at low = {expected_price}, "
            f"got {trade['price']}"
        )
        assert trade["trade_date"] == "2026-05-02"

    def test_fill_price_uses_candle_path_for_bearish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bearish candle (close < open): BUY fills at open (price_path[0])."""
        buy_strategy = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
        runner = _make_runner(
            source_code=buy_strategy,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 3),
            monkeypatch=monkeypatch,
            fill_price_mode="current_intraday",
        )
        # Bar 2 bearish: open=11.5, close=11.0 (close < open)
        klines = [
            _make_bar(date(2026, 5, 1), open_=10.0, close=10.0),
            _make_bar(date(2026, 5, 2), open_=11.5, high=11.6, low=10.9, close=11.0),
            _make_bar(date(2026, 5, 3), open_=12.0, close=12.5),
        ]
        result = runner.run({"000001.SZ": klines})

        assert result["trade_count"] >= 1
        trade = result["trade_records"][0]
        # Bearish: fill at open = 11.5 * (1 + 0.001)
        expected_price = float(Decimal("11.5") * Decimal("1.001"))
        assert trade["price"] == pytest.approx(expected_price, rel=1e-6), (
            f"current_intraday bearish: fill at open = {expected_price}, "
            f"got {trade['price']}"
        )


# ---------------------------------------------------------------------------
# Test 5: config field validation
# ---------------------------------------------------------------------------


class TestFillPriceModeConfig:
    """BACKTEST_FILL_PRICE_MODE config field validation."""

    def test_default_is_next_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default value should be 'next_open'."""
        import app.core.config as config_module

        config_module.get_settings.cache_clear()
        try:
            monkeypatch.delenv("BACKTEST_FILL_PRICE_MODE", raising=False)
            settings = config_module.Settings(
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db"
            )
            assert settings.backtest_fill_price_mode == "next_open"
        finally:
            config_module.get_settings.cache_clear()

    @pytest.mark.parametrize(
        "valid_mode", ["next_open", "current_close", "current_intraday", "NEXT_OPEN", " Current_Close "]
    )
    def test_valid_modes_accepted(self, valid_mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid mode values should be accepted (case-insensitive, trimmed)."""
        import app.core.config as config_module

        config_module.get_settings.cache_clear()
        try:
            monkeypatch.setenv("BACKTEST_FILL_PRICE_MODE", valid_mode)
            settings = config_module.Settings(
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db"
            )
            assert settings.backtest_fill_price_mode == valid_mode.strip().lower()
        finally:
            config_module.get_settings.cache_clear()
            monkeypatch.delenv("BACKTEST_FILL_PRICE_MODE", raising=False)

    def test_invalid_mode_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid mode values should raise ValueError."""
        import app.core.config as config_module

        config_module.get_settings.cache_clear()
        try:
            monkeypatch.setenv("BACKTEST_FILL_PRICE_MODE", "invalid_mode")
            with pytest.raises(ValueError, match="BACKTEST_FILL_PRICE_MODE"):
                config_module.Settings(
                    DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db"
                )
        finally:
            config_module.get_settings.cache_clear()
            monkeypatch.delenv("BACKTEST_FILL_PRICE_MODE", raising=False)


# ---------------------------------------------------------------------------
# Test 6: lot entry_date uses fill_bar.trade_date
# ---------------------------------------------------------------------------


@pytest.mark.backtest
class TestLotEntryDateUsesFillBarDate:
    """When fill_bar is provided (next_open mode), the lot's entry_date
    must equal fill_bar.trade_date, NOT bar.trade_date.

    This is critical for T+1 enforcement: a lot bought "as of td" but
    filled on td+1 must be sellable starting from td+2, not td+1.
    """

    def test_lot_entry_date_is_fill_bar_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_open_lots[ts_code][-1].entry_date == fill_bar.trade_date."""
        buy_strategy = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
        runner = _make_runner(
            source_code=buy_strategy,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 3),
            monkeypatch=monkeypatch,
            fill_price_mode="next_open",
        )
        klines = [
            _make_bar(date(2026, 5, 1), open_=10.0, close=10.0),
            _make_bar(date(2026, 5, 2), open_=10.5, close=10.5),
            _make_bar(date(2026, 5, 3), open_=11.0, close=11.0),
        ]
        runner.run({"000001.SZ": klines})

        # After the run, the lot entry_date should be the fill_bar's date.
        lots = runner._open_lots.get("000001.SZ", [])
        assert len(lots) >= 1, "Expected at least one open lot after buy"
        # Signal on May 2 (window=[bar1]), fill on May 3 (fill_bar=bar3)
        assert lots[-1].entry_date == date(2026, 5, 3), (
            f"Lot entry_date should be fill_bar.trade_date (2026-05-03), "
            f"got {lots[-1].entry_date}"
        )
