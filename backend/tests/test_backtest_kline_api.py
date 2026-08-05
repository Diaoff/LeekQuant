"""Regression tests for lazy backtest K-line loading."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api import backtests


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class CaptureSession:
    def __init__(self, results=None):
        self.statements = []
        self.params = []
        self.results = list(results or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        if self.results:
            return self.results.pop(0)
        return FakeResult([])


class FakeRequest:
    headers = {"X-User-ID": "1"}
    query_params = {}


@pytest.mark.asyncio
async def test_get_backtest_klines_returns_requested_stock_range(monkeypatch) -> None:
    calls = []

    async def fake_get_klines(session, ts_code, start_date, end_date):
        calls.append((session, ts_code, start_date, end_date))
        return [
            {
                "trade_date": date(2026, 1, 5),
                "open": Decimal("10.00"),
                "high": Decimal("10.80"),
                "low": Decimal("9.90"),
                "close": Decimal("10.50"),
                "volume": Decimal("12300"),
            }
        ]

    monkeypatch.setattr(backtests, "get_klines", fake_get_klines)
    session = CaptureSession(
        [
            FakeResult(
                [
                    {
                        "start_date": date(2026, 1, 1),
                        "end_date": date(2026, 1, 31),
                    }
                ]
            )
        ]
    )

    response = await backtests.get_backtest_klines(7, FakeRequest(), "000001.SZ", session)

    assert response == [
        {
            "date": "2026-01-05",
            "open": 10.0,
            "high": 10.8,
            "low": 9.9,
            "close": 10.5,
            "volume": 12300,
        }
    ]
    assert calls == [(session, "000001.SZ", date(2026, 1, 1), date(2026, 1, 31))]


@pytest.mark.asyncio
async def test_get_backtest_klines_returns_empty_when_market_data_missing(monkeypatch) -> None:
    async def fake_get_klines(session, ts_code, start_date, end_date):
        return []

    monkeypatch.setattr(backtests, "get_klines", fake_get_klines)
    session = CaptureSession(
        [
            FakeResult(
                [
                    {
                        "start_date": date(2026, 1, 1),
                        "end_date": date(2026, 1, 31),
                    }
                ]
            )
        ]
    )

    assert await backtests.get_backtest_klines(7, FakeRequest(), "600000.SH", session) == []


@pytest.mark.asyncio
async def test_get_backtest_klines_404s_for_inaccessible_backtest(monkeypatch) -> None:
    async def fake_get_klines(session, ts_code, start_date, end_date):
        raise AssertionError("market data should not be queried for missing backtest")

    monkeypatch.setattr(backtests, "get_klines", fake_get_klines)
    session = CaptureSession([FakeResult([])])

    with pytest.raises(HTTPException) as exc:
        await backtests.get_backtest_klines(7, FakeRequest(), "600000.SH", session)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_backtest_uses_legacy_trade_records_when_child_tables_empty(monkeypatch) -> None:
    """Regression: 规范化改造前（子表为空）的历史回测，逐笔明细仍存于
    backtest_results.trade_records（完整 JSON）。get_backtest 应回退到它，
    否则历史回测的交易明细会显示为空。"""
    legacy = [
        {
            "ts_code": "600000.SH",
            "trade_date": date(2026, 1, 5),
            "direction": "买入",
            "action": "BUY",
            "price": 10.5,
            "volume": 100,
            "amount": 1050.0,
            "commission": 1.0,
            "stamp_tax": 0.0,
            "transfer_fee": 0.0,
            "total_fee": 1.0,
            "pnl": 0.0,
            "signal_reason": "test",
            "target_position": 0.5,
            "position_before": 0,
            "position_after": 100,
            "balance_before": 10000.0,
            "balance_after": 8950.0,
            "holding_days": 0,
            "exit_reason": None,
        }
    ]
    row = {
        "id": 147,
        "status": "success",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 1, 31),
        "created_at": None,
        "params_snapshot": None,
        "performance": {"daily_returns": []},
        "trade_records": legacy,
        "total_return": 0.0,
        "annual_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "win_rate": 0.0,
        "trade_count": 1,
        "final_value": 10000.0,
        "initial_capital": 10000.0,
        "strategy_id": 15,
        "strategy_name": "x",
        "benchmark_return": 0.0,
    }
    session = CaptureSession([FakeResult([row])])
    monkeypatch.setattr(backtests, "_with_target_fields", lambda r: r)

    async def fake_klines(*_a, **_k):
        return []

    monkeypatch.setattr(backtests, "get_klines", fake_klines)

    resp = await backtests.get_backtest(
        147, FakeRequest(), include_kline=False, detail_limit=20000, session=session
    )

    assert resp["trade_records"] == legacy
