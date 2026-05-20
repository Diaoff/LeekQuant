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


class CaptureSession:
    def __init__(self, results=None):
        self.statements = []
        self.params = []
        self.results = list(results or [])

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
