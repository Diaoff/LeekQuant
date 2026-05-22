from datetime import date
from decimal import Decimal

import pytest

from app.tasks.signal_tasks import generate_all_signals_for_date


class FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = 1

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._scalar


class ScriptedSession:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.params = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        return self.results.pop(0) if self.results else FakeResult([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def kline_rows():
    rows = []
    for day in (21, 20):
        rows.append(
            {
                "ts_code": "000001.SZ",
                "trade_date": date(2026, 5, day),
                "open": Decimal("10.0000"),
                "high": Decimal("10.2000"),
                "low": Decimal("9.9000"),
                "close": Decimal("10.1000"),
                "pre_close": Decimal("10.0000"),
                "volume": 100000,
                "amount": Decimal("1010000.0000"),
                "adj_factor": None,
                "is_suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
            }
        )
    return rows


@pytest.mark.asyncio
async def test_generate_all_signals_logs_full_market_signal_without_account():
    session = ScriptedSession(
        [
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '买入', 'target_position': 1, 'current_position': 0}"}]),
            FakeResult([{"ts_code": "000001.SZ"}]),
            FakeResult([]),
            FakeResult(kline_rows()),
            FakeResult([{"id": 77}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 1
    assert result["orders_created"] == 0
    assert result["error_count"] == 0
    assert "INSERT INTO signal_log" in session.statements[4]
    assert session.params[4]["account_id"] is None
    assert session.params[4]["strategy_id"] == 3


@pytest.mark.asyncio
async def test_generate_all_signals_collects_strategy_errors_and_continues():
    session = ScriptedSession(
        [
            FakeResult([
                {"id": 3, "user_id": 1, "name": "bad", "source_code": "def generate_signal(ctx):\n    raise RuntimeError('boom')"},
                {"id": 4, "user_id": 1, "name": "good", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '观望', 'current_position': 0}"},
            ]),
            FakeResult([{"ts_code": "000001.SZ"}]),
            FakeResult([]),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult(kline_rows()),
            FakeResult([{"id": 78}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 1
    assert result["error_count"] == 1
    assert result["errors"][0]["strategy_id"] == 3
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_generate_all_signals_creates_order_for_bound_account(monkeypatch):
    from app.tasks import signal_tasks

    async def fake_generate_order_from_signal(session, *, user_id, account_id, request):
        return {"order": {"id": 9}, "signal": {"id": 8}}

    monkeypatch.setattr(signal_tasks, "generate_order_from_signal", fake_generate_order_from_signal)
    session = ScriptedSession(
        [
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '买入', 'target_position': 1, 'current_position': 0}"}]),
            FakeResult([{"ts_code": "000001.SZ"}]),
            FakeResult([{"id": 5, "user_id": 1}]),
            FakeResult(kline_rows()),
            FakeResult([{"id": 77}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 1
    assert result["orders_created"] == 1
