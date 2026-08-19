"""Tests for ctx.fundamentals injection in the backtest engine (no-lookahead).

Verifies:
1. No fundamentals injected -> ctx.fundamentals is None (graceful degrade).
2. announce_date == current bar (td) is NOT visible (lookahead protection):
   strategy decides as of td-1, so only reports with announce_date <= td-1 appear.
3. Most recent report with announce_date <= td-1 is picked (not the older one).
4. Only future announcements -> None.
5. Missing fields stay None on the snapshot.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.backtest import adapter as adapter_module
from app.backtest.adapter import BacktestConfig, BacktestContext, BacktestRunner, KBar
from app.backtest.strategy_runtime import StrategyExecutionResult


def _make_bar(
    trade_date: date,
    *,
    close: Decimal | float = 10.0,
    ts_code: str = "000001.SZ",
) -> KBar:
    open_d = Decimal(str(close))
    close_d = Decimal(str(close))
    return KBar(
        ts_code=ts_code,
        trade_date=trade_date,
        open=open_d,
        high=open_d + Decimal("0.2"),
        low=open_d - Decimal("0.2"),
        close=close_d,
        pre_close=close_d,
        volume=1_000_000,
        amount=open_d * 1_000_000,
        adj_factor=None,
        is_suspended=False,
        is_limit_up=False,
        is_limit_down=False,
        turnover_rate=None,
    )


def _make_config(**kwargs: Any) -> BacktestConfig:
    return BacktestConfig(
        strategy_id=1,
        source_code="def generate_signal(ctx):\n    return None\n",
        stock_pool=["000001.SZ"],
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 5),
        initial_cash=Decimal("100000"),
        slippage_pct=0.001,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def patch_position_setter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same workaround as test_backtest_lookahead.py: install a setter for
    the read-only BacktestContext.current_position property."""
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


def _run_capture(
    monkeypatch: pytest.MonkeyPatch,
    klines: list[KBar],
    fundamentals: dict[str, list[dict[str, Any]]] | None,
) -> list[Any]:
    """Run backtest capturing ctx.fundamentals into a list at each bar."""
    captured: list[Any] = []

    def fake_execute_signal(compiled: dict[str, Any], ctx: BacktestContext, **_kwargs: Any) -> StrategyExecutionResult:
        captured.append(ctx.fundamentals)
        return StrategyExecutionResult(ok=True, signal=None)

    monkeypatch.setattr(adapter_module, "execute_compiled_signal", fake_execute_signal)
    runner = BacktestRunner(_make_config())
    runner.run({"000001.SZ": klines}, fundamentals=fundamentals)
    return captured


def _fund_rows(announce_dates: list[str], **extra: Any) -> list[dict[str, Any]]:
    rows = []
    for i, ad in enumerate(announce_dates):
        row = {
            "ts_code": "000001.SZ",
            "report_date": date(2026, 3, 31),
            "announce_date": date.fromisoformat(ad),
            "roe": Decimal(str(10 + i)),
            "revenue_growth": Decimal("5"),
            "net_profit_growth": Decimal("20"),
            "gross_margin": Decimal("30"),
            "net_profit": Decimal("100"),
        }
        row.update(extra)
        rows.append(row)
    return rows


def test_fundamentals_none_when_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    klines = [_make_bar(date(2026, 5, 2)), _make_bar(date(2026, 5, 3))]
    captured = _run_capture(monkeypatch, klines, fundamentals=None)
    assert captured and all(c is None for c in captured)


def test_fundamentals_hides_same_day_announcement(monkeypatch: pytest.MonkeyPatch) -> None:
    """announce_date == td (May 3) must NOT be visible when deciding on May 3.

    Strategy window excludes td's bar; ctx.trade_date == May 2, so a report
    announced May 3 is future info and must be filtered out.
    """
    klines = [
        _make_bar(date(2026, 5, 1)),
        _make_bar(date(2026, 5, 2)),
        _make_bar(date(2026, 5, 3)),
    ]
    fund = {"000001.SZ": _fund_rows(["2026-05-03"], roe=Decimal("99"))}
    captured = _run_capture(monkeypatch, klines, fundamentals=fund)
    # td=May2 (trade_date=May1): no announcement <= May1 -> None
    # td=May3 (trade_date=May2): announcement May3 > May2 -> still None
    assert all(c is None for c in captured), f"same-day announcement leaked: {captured}"


def test_fundamentals_picks_most_recent_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two reports announced May 1 and May 2; deciding on May 3 (td-1=May 2)
    must see the May-2 one (the freshest), not the May-1 one."""
    klines = [
        _make_bar(date(2026, 5, 1)),
        _make_bar(date(2026, 5, 2)),
        _make_bar(date(2026, 5, 3)),
    ]
    fund = {
        "000001.SZ": [
            *_fund_rows(["2026-05-01"], roe=Decimal("11")),
            *_fund_rows(["2026-05-02"], roe=Decimal("22")),
        ]
    }
    captured = _run_capture(monkeypatch, klines, fundamentals=fund)
    # td=May2 -> sees May-1 report (roe=11); td=May3 -> sees May-2 report (roe=22)
    seen = [c for c in captured if c is not None]
    assert len(seen) == 2
    assert seen[0].roe == Decimal("11")
    assert seen[1].roe == Decimal("22")


def test_fundamentals_none_when_only_future_announcements(monkeypatch: pytest.MonkeyPatch) -> None:
    klines = [
        _make_bar(date(2026, 5, 1)),
        _make_bar(date(2026, 5, 2)),
        _make_bar(date(2026, 5, 3)),
    ]
    fund = {"000001.SZ": _fund_rows(["2026-06-01"], roe=Decimal("50"))}
    captured = _run_capture(monkeypatch, klines, fundamentals=fund)
    assert all(c is None for c in captured)


def test_fundamentals_snapshot_missing_fields_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    klines = [_make_bar(date(2026, 5, 1)), _make_bar(date(2026, 5, 2))]
    fund = {"000001.SZ": _fund_rows(["2026-04-30"], roe=None, net_profit_growth=None)}
    captured = _run_capture(monkeypatch, klines, fundamentals=fund)
    # 首日(May1)窗口空被跳过；May2 决策日=May1，announce 04-30 可见
    assert len(captured) == 1
    snap = captured[0]
    assert snap is not None
    assert snap.roe is None
    assert snap.net_profit_growth is None
    assert snap.revenue_growth is not None
    assert snap.as_dict()["roe"] is None
