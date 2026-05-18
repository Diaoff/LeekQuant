"""Shared test fixtures for M3 backtest module tests."""
from datetime import date
from decimal import Decimal

import pytest

from app.backtest.adapter import BacktestConfig, KBar


class FakeResult:
    """Mock database result for unit testing without real DB."""

    def __init__(self, rows=None, scalar=None, rowcount=1):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar if self._scalar is not None else self._rows[0]

    def scalar_one_or_none(self):
        return self._scalar


class CaptureSession:
    """Capture SQL statements and parameters for verification."""

    def __init__(self, results=None):
        self.statements = []
        self.params = []
        self.commits = 0
        self.results = list(results or [])

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        if self.results:
            return self.results.pop(0)
        return FakeResult([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


def sample_kbar(
    ts_code: str = "000001.SZ",
    trade_date: date | None = None,
    open_price: float = 10.0,
    high: float = 10.5,
    low: float = 9.5,
    close: float = 10.2,
    pre_close: float = 10.0,
    volume: int = 1000000,
    amount: float = 10200000.0,
    is_suspended: bool = False,
    is_limit_up: bool = False,
    is_limit_down: bool = False,
) -> KBar:
    """Factory function to generate test KBar data."""
    return KBar(
        ts_code=ts_code,
        trade_date=trade_date or date(2026, 5, 1),
        open=Decimal(str(open_price)),
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


def sample_backtest_config(
    strategy_id: int = 1,
    source_code: str = "",
    stock_pool: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    initial_cash: Decimal | None = None,
) -> BacktestConfig:
    """Factory function to generate test BacktestConfig."""
    return BacktestConfig(
        strategy_id=strategy_id,
        source_code=source_code,
        stock_pool=stock_pool or ["000001.SZ"],
        start_date=start_date or date(2026, 5, 1),
        end_date=end_date or date(2026, 5, 15),
        initial_cash=initial_cash or Decimal("100000"),
    )


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "backtest: marks tests related to backtest engine")
    config.addinivalue_line("markers", "signals: marks tests for signal state machine")
    config.addinivalue_line("markers", "cost: marks tests for cost calculator")
