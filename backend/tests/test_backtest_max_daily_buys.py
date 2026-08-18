"""Tests for the max_daily_buys (单日最大买入只数) backtest config option.

Verifies that:
1. max_daily_buys = 0 (default) imposes no limit — every buy candidate on a
   given fill date is executed.
2. max_daily_buys = N limits the number of distinct stocks actually purchased
   on each trading day to at most N, while still allowing the remaining
   candidates to fill on subsequent days (so positions are built smoothly
   instead of all at once).
3. The per-day cap is keyed by the execution (fill) date, not the signal date,
   and a blocked buy is recorded as a BLOCKED signal with the limit reason.
4. The option survives the runtime + strategy config merge in tasks.py.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar
from app.backtest.tasks import _merge_backtest_config


def _make_bar(
    trade_date: date,
    *,
    open_: float = 10.0,
    ts_code: str = "000001.SZ",
) -> KBar:
    open_d = Decimal(str(open_))
    return KBar(
        ts_code=ts_code,
        trade_date=trade_date,
        open=open_d,
        high=open_d + Decimal("0.2"),
        low=open_d - Decimal("0.2"),
        close=open_d,
        pre_close=open_d,
        volume=1_000_000,
        amount=open_d * 1_000_000,
        adj_factor=None,
        is_suspended=False,
        is_limit_up=False,
        is_limit_down=False,
        turnover_rate=None,
    )


BUY_ALL_STRATEGY = '''
def generate_signal(ctx):
    if len(ctx.close) == 0:
        return None
    # 20% allocation so three stocks can coexist without exhausting cash;
    # this isolates the daily-buy cap from the cash constraint.
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.2}
'''


def _build_klines(stock_codes: list[str], days: int = 12) -> dict[str, list[KBar]]:
    # Flat prices so a stock bought at its target weight stays at target and is
    # not repeatedly "topped up" each day — this isolates the daily-buy cap
    # from the engine's target-position rebalancing behavior.
    start = date(2026, 5, 1)
    klines: dict[str, list[KBar]] = {}
    for code in stock_codes:
        bars = [
            _make_bar(start + timedelta(days=i), open_=10.0, ts_code=code)
            for i in range(days)
        ]
        klines[code] = bars
    return klines


def _make_runner(
    *,
    max_daily_buys: int = 0,
    stock_pool: list[str] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    **config_kwargs: Any,
) -> BacktestRunner:
    runner = BacktestRunner(
        BacktestConfig(
            strategy_id=1,
            source_code=BUY_ALL_STRATEGY,
            stock_pool=stock_pool or ["000001.SZ", "000002.SZ", "000003.SZ"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 12),
            initial_cash=Decimal("100000"),
            slippage_pct=0,
            max_daily_buys=max_daily_buys,
            **config_kwargs,
        )
    )
    if monkeypatch is not None:
        # next_open fill mode resolves fill price from the next day's open;
        # qfq adjust mode is a no-op. Patch directly to avoid settings deps.
        monkeypatch.setattr(runner, "_fill_price_mode", lambda: "next_open")
        monkeypatch.setattr(runner, "_adjust_mode", lambda: "qfq")
    return runner


def test_max_daily_buys_zero_is_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the default (0), all three stocks are bought on the first fill date."""
    runner = _make_runner(max_daily_buys=0, monkeypatch=monkeypatch)
    result = runner.run(_build_klines(["000001.SZ", "000002.SZ", "000003.SZ"]))

    trades = result["trade_records"]
    assert len(trades) == 3, f"expected 3 buys, got {len(trades)}"
    # All three should execute on the same fill date (no per-day cap).
    dates = {t["trade_date"] for t in trades}
    assert len(dates) == 1, f"expected all buys on one date, got {dates}"
    traded = {t["ts_code"] for t in trades}
    assert traded == {"000001.SZ", "000002.SZ", "000003.SZ"}


def test_max_daily_buys_limits_distinct_stocks_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """With cap=1, at most one distinct stock is bought per trading day."""
    runner = _make_runner(max_daily_buys=1, monkeypatch=monkeypatch)
    result = runner.run(_build_klines(["000001.SZ", "000002.SZ", "000003.SZ"]))

    trades = result["trade_records"]
    # All three are eventually bought, but spread across days.
    traded = {t["ts_code"] for t in trades}
    assert traded == {"000001.SZ", "000002.SZ", "000003.SZ"}

    # No trading day executes more than one distinct-stock buy.
    by_date: dict[str, set[str]] = {}
    for t in trades:
        by_date.setdefault(t["trade_date"], set()).add(t["ts_code"])
    for d, codes in by_date.items():
        assert len(codes) <= 1, f"date {d} bought {len(codes)} stocks: {codes}"


def test_max_daily_buys_blocks_excess_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Candidates beyond the daily cap are recorded as BLOCKED with the reason."""
    runner = _make_runner(max_daily_buys=1, monkeypatch=monkeypatch)
    runner.run(_build_klines(["000001.SZ", "000002.SZ", "000003.SZ"]))

    blocked = [
        s for s in runner.signals
        if s.get("match_status") == "BLOCKED"
        and "每日最大买入只数限制" in (s.get("reason") or "")
    ]
    assert blocked, "expected BLOCKED signals due to the daily buy cap"
    # 000002.SZ and 000003.SZ should both be blocked on the first fill date.
    blocked_codes = {s["ts_code"] for s in blocked}
    assert {"000002.SZ", "000003.SZ"} <= blocked_codes


def test_max_daily_buys_cap_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """With cap=2, at most two distinct stocks are bought per trading day."""
    runner = _make_runner(max_daily_buys=2, monkeypatch=monkeypatch)
    result = runner.run(_build_klines(["000001.SZ", "000002.SZ", "000003.SZ"]))

    trades = result["trade_records"]
    by_date: dict[str, set[str]] = {}
    for t in trades:
        by_date.setdefault(t["trade_date"], set()).add(t["ts_code"])
    for d, codes in by_date.items():
        assert len(codes) <= 2, f"date {d} bought {len(codes)} stocks: {codes}"
    assert {t["ts_code"] for t in trades} == {"000001.SZ", "000002.SZ", "000003.SZ"}


def test_merge_backtest_config_carries_max_daily_buys() -> None:
    merged = _merge_backtest_config(
        {"rebalance_mode": "disabled"},
        {"config": {"max_daily_buys": 3}},
    )
    assert merged.get("max_daily_buys") == 3
