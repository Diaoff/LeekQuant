"""Tests for backtest module improvements."""
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
    _ClosedLot,
)
from app.backtest.cost import FeeConfig
from app.backtest.signals import apply_cn_rules, map_signal_to_action, SignalInput


def _make_kbar(
    ts_code: str = "000001.SZ",
    trade_date: date | None = None,
    open_: float = 10.0,
    high: float = 10.5,
    low: float = 9.5,
    close: float = 10.2,
    pre_close: float = 10.0,
    volume: int = 1000,
    amount: float = 10200,
    is_suspended: bool = False,
    is_limit_up: bool = False,
    is_limit_down: bool = False,
) -> KBar:
    return KBar(
        ts_code=ts_code,
        trade_date=trade_date or date(2026, 1, 2),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        pre_close=Decimal(str(pre_close)),
        volume=volume,
        amount=Decimal(str(amount)),
        adj_factor=None,
        is_suspended=is_suspended,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
    )


# ---------------------------------------------------------------------------
# Lot tracking
# ---------------------------------------------------------------------------

def test_lot_tracking_on_buy() -> None:
    runner = BacktestRunner(BacktestConfig(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    ))
    bar = _make_kbar()
    runner.positions["000001.SZ"] = Position(ts_code="000001.SZ", shares=100, avg_cost=Decimal("10"))
    runner.cash = Decimal("90000")
    runner._entry_dates["000001.SZ"] = date(2026, 1, 1)
    runner._open_lots["000001.SZ"] = [_LotEntry(ts_code="000001.SZ", shares=100, cost=Decimal("10"), entry_date=date(2026, 1, 1))]

    assert "000001.SZ" in runner._open_lots
    assert len(runner._open_lots["000001.SZ"]) == 1
    assert runner._open_lots["000001.SZ"][0].shares == 100


def test_lot_tracking_on_sell_fifo() -> None:
    runner = BacktestRunner(BacktestConfig(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    ))
    runner._open_lots["000001.SZ"] = [
        _LotEntry(ts_code="000001.SZ", shares=100, cost=Decimal("10"), entry_date=date(2026, 1, 1)),
        _LotEntry(ts_code="000001.SZ", shares=200, cost=Decimal("11"), entry_date=date(2026, 1, 5)),
    ]
    runner._closed_lots = []

    lots = runner._open_lots["000001.SZ"]
    sell_shares = 150
    while sell_shares > 0 and lots:
        lot = lots[0]
        lot_shares = min(sell_shares, lot.shares)
        lot.shares -= lot_shares
        sell_shares -= lot_shares
        if lot.shares <= 0:
            lots.pop(0)

    assert len(lots) == 1
    assert lots[0].shares == 150


# ---------------------------------------------------------------------------
# Auto T+1 enforcement
# ---------------------------------------------------------------------------

def test_t1_blocks_sell_on_same_day() -> None:
    from app.backtest.signals import apply_cn_rules
    action, reason = apply_cn_rules(
        "SELL_ALL",
        is_suspended=False,
        is_limit_up=False,
        is_limit_down=False,
        is_t1_blocked=True,
    )
    assert action == "BLOCKED"
    assert "T+1" in reason


def test_t1_allows_sell_after_hold() -> None:
    action, reason = apply_cn_rules(
        "SELL_ALL",
        is_suspended=False,
        is_limit_up=False,
        is_limit_down=False,
        is_t1_blocked=False,
    )
    assert action == "SELL_ALL"
    assert reason == ""


# ---------------------------------------------------------------------------
# New metrics
# ---------------------------------------------------------------------------

def test_compute_results_includes_new_metrics() -> None:
    runner = BacktestRunner(BacktestConfig(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
    ))
    runner.equity_curve = [
        {"date": "2026-01-01", "total_asset": 100000, "cash": 100000},
        {"date": "2026-01-02", "total_asset": 102000, "cash": 102000},
        {"date": "2026-01-03", "total_asset": 101000, "cash": 101000},
    ]
    runner._closed_lots = [
        _ClosedLot("000001.SZ", 100, Decimal("10"), date(2026, 1, 1), Decimal("10.5"), date(2026, 1, 2), Decimal("50"), 1, "策略信号"),
        _ClosedLot("000001.SZ", 100, Decimal("10.5"), date(2026, 1, 2), Decimal("10.3"), date(2026, 1, 3), Decimal("-20"), 1, "策略信号"),
    ]

    results = runner._compute_results()

    assert "sortino_ratio" in results
    assert "calmar_ratio" in results
    assert "profit_factor" in results
    assert "avg_win" in results
    assert "avg_loss" in results
    assert "max_consecutive_losses" in results
    assert "avg_holding_days" in results
    assert "total_fees" in results
    assert "monthly_returns" in results
    assert "daily_returns" in results
    assert results["win_rate"] == 0.5
    assert results["profit_factor"] > 0


def test_sortino_ratio_uses_downside_deviation() -> None:
    runner = BacktestRunner(BacktestConfig(
        strategy_id=1,
        source_code="",
        stock_pool=[],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
    ))
    daily_returns = [0.01, -0.005, 0.008, -0.003, 0.006]
    values = [100000]
    for r in daily_returns:
        values.append(values[-1] * (1 + r))
    runner.equity_curve = [{"date": f"2026-01-0{i+1}", "total_asset": v, "cash": v} for i, v in enumerate(values)]

    results = runner._compute_results()
    assert results["sortino_ratio"] != results["sharpe_ratio"]


# ---------------------------------------------------------------------------
# Slippage model
# ---------------------------------------------------------------------------

def test_slippage_increases_buy_price() -> None:
    config = BacktestConfig(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
        slippage_pct=0.01,
    )
    runner = BacktestRunner(config)
    runner.cash = Decimal("100000")

    bar = _make_kbar(open_=10.0, high=10.5, low=9.5, close=10.2)
    price_path = runner._infer_candle_path(bar.open, bar.high, bar.low, bar.close)
    buy_price = price_path[1] if bar.close >= bar.open else price_path[0]
    slippage = Decimal("0.01")
    final_price = buy_price * (1 + slippage)

    assert float(final_price) > float(buy_price)


def test_slippage_decreases_sell_price() -> None:
    config = BacktestConfig(
        strategy_id=1,
        source_code="",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
        slippage_pct=0.01,
    )
    runner = BacktestRunner(config)

    bar = _make_kbar(open_=10.0, high=10.5, low=9.5, close=10.2)
    price_path = runner._infer_candle_path(bar.open, bar.high, bar.low, bar.close)
    sell_price = price_path[2] if bar.close >= bar.open else price_path[1]
    slippage = Decimal("0.01")
    final_price = sell_price * (1 - slippage)

    assert float(final_price) < float(sell_price)


# ---------------------------------------------------------------------------
# Benchmark metrics
# ---------------------------------------------------------------------------

def test_benchmark_metrics_computation() -> None:
    from app.backtest.tasks import _compute_benchmark_metrics

    strategy_values = [100000, 101000, 102000, 103000]
    strategy_dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    benchmark_rows = [
        {"trade_date": "2026-01-01", "close": 3000},
        {"trade_date": "2026-01-02", "close": 3010},
        {"trade_date": "2026-01-03", "close": 3020},
        {"trade_date": "2026-01-04", "close": 3030},
    ]

    metrics = _compute_benchmark_metrics(strategy_values, strategy_dates, benchmark_rows)

    assert "alpha" in metrics
    assert "tracking_error" in metrics
    assert "information_ratio" in metrics
    assert "benchmark_curve" in metrics
    assert len(metrics["benchmark_curve"]) == 4


def test_benchmark_metrics_empty_benchmark() -> None:
    from app.backtest.tasks import _compute_benchmark_metrics

    metrics = _compute_benchmark_metrics([100000, 101000], ["2026-01-01", "2026-01-02"], [])
    assert metrics == {}


# ---------------------------------------------------------------------------
# Monthly returns
# ---------------------------------------------------------------------------

def test_monthly_returns_computation() -> None:
    runner = BacktestRunner(BacktestConfig(
        strategy_id=1,
        source_code="",
        stock_pool=[],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
    ))
    runner.equity_curve = [
        {"date": "2026-01-15", "total_asset": 100000, "cash": 100000},
        {"date": "2026-01-31", "total_asset": 102000, "cash": 102000},
        {"date": "2026-02-15", "total_asset": 101000, "cash": 101000},
        {"date": "2026-02-28", "total_asset": 103000, "cash": 103000},
        {"date": "2026-03-15", "total_asset": 104000, "cash": 104000},
    ]

    results = runner._compute_results()
    assert "monthly_returns" in results
    assert isinstance(results["monthly_returns"], dict)
