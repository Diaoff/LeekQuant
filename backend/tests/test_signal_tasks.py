from datetime import date, datetime
from decimal import Decimal

import pytest

from app.realtime.models import RealtimeTick
from app.backtest.strategy_runtime import StrategyExecutionResult
from app.tasks.signal_tasks import generate_all_signals_for_date, generate_intraday_position_signals_for_date


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

    def scalar_one(self):
        return self._scalar if self._scalar is not None else self._rows[0]


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

    def begin_nested(self):
        class _Nested:
            async def __aenter__(self, *_a, **_kw): pass
            async def __aexit__(self, *_a, **_kw): pass
        return _Nested()


@pytest.fixture(autouse=True)
def patch_strategy_exec(monkeypatch):
    from app.tasks import signal_tasks

    def fake_exec_strategy(source_code, ctx):
        sandbox = {"ctx": ctx}
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

    monkeypatch.setattr(signal_tasks, "_exec_strategy", fake_exec_strategy)


def kline_rows():
    return kline_rows_for("000001.SZ")


def kline_rows_for(ts_code):
    close_by_code = {
        "000001.SZ": Decimal("10.1000"),
        "000002.SZ": Decimal("10.2000"),
        "000003.SZ": Decimal("10.3000"),
        "000004.SZ": Decimal("10.4000"),
    }
    close = close_by_code.get(ts_code, Decimal("10.1000"))
    rows = []
    for day in (21, 20):
        rows.append(
            {
                "ts_code": ts_code,
                "trade_date": date(2026, 5, day),
                "open": Decimal("10.0000"),
                "high": Decimal("10.2000"),
                "low": Decimal("9.9000"),
                "close": close,
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


def batch_kline_rows(*ts_codes):
    rows = []
    for ts_code in ts_codes:
        rows.extend(kline_rows_for(ts_code))
    return rows


@pytest.mark.asyncio
async def test_generate_all_signals_logs_strategy_signal_without_account():
    session = ScriptedSession(
        [
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '买入', 'target_position': 1, 'current_position': 0}"}]),
            FakeResult([{"ts_code": "000001.SZ"}]),
            FakeResult([]),
            FakeResult([]),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult([{"id": 77}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 1
    assert result["orders_created"] == 0
    assert result["orders_skipped"] == 0
    assert result["error_count"] == 0
    assert any("account_id IS NULL" in statement for statement in session.statements)
    assert any("INSERT INTO signal_log" in statement for statement in session.statements)


@pytest.mark.asyncio
async def test_generate_all_signals_without_explicit_date_uses_latest_synced_kline():
    session = ScriptedSession(
        [
            FakeResult(scalar=date(2026, 5, 21)),
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '观望', 'current_position': 0}"}]),
            FakeResult([{"ts_code": "000001.SZ"}]),
            FakeResult([]),
            FakeResult([]),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult([{"id": 77}]),
        ]
    )

    result = await generate_all_signals_for_date(session)

    assert result["trade_date"] == "2026-05-21"
    assert result["signals_logged"] == 1
    assert "SELECT MAX(trade_date)" in session.statements[0]
    assert any(params.get("trade_date") == date(2026, 5, 21) for params in session.params)


@pytest.mark.asyncio
async def test_generate_all_signals_without_kline_falls_back_to_latest_open_calendar_day():
    session = ScriptedSession(
        [
            FakeResult(scalar=None),
            FakeResult(scalar=date(2026, 5, 20)),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    result = await generate_all_signals_for_date(session)

    assert result["trade_date"] == "2026-05-20"
    assert result["strategy_count"] == 0
    assert "FROM trade_calendar" in session.statements[1]


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
            FakeResult([]),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult([{"id": 78}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 1
    assert result["error_count"] == 1
    assert result["errors"][0]["strategy_id"] == 3
    assert result["errors"][0]["error_type"] == "RuntimeError"
    assert result["errors"][0]["error_message"] == "boom"
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_generate_all_signals_creates_order_for_bound_account(monkeypatch):
    from app.tasks import signal_tasks

    calls = []

    async def fake_generate_order_from_signal(
        session,
        *,
        user_id,
        account_id,
        request,
        strategy_signal_id=None,
        auto_commit=True,
        auto_match=False,
        auto_match_mode="close",
    ):
        calls.append(
            {
                "user_id": user_id,
                "account_id": account_id,
                "strategy_signal_id": strategy_signal_id,
                "auto_commit": auto_commit,
                "auto_match": auto_match,
                "auto_match_mode": auto_match_mode,
            }
        )
        return {"order": {"id": 9}, "signal": None}

    monkeypatch.setattr(signal_tasks, "generate_order_from_signal", fake_generate_order_from_signal)
    session = ScriptedSession(
        [
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '买入', 'target_position': 1, 'current_position': 0}"}]),
            FakeResult([{"ts_code": "000001.SZ"}]),
            FakeResult([]),
            FakeResult([{"id": 5, "user_id": 1}]),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult([{"id": 77}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 1
    assert result["orders_created"] == 1
    assert result["orders_skipped"] == 0
    assert calls == [
        {
            "user_id": 1,
            "account_id": 5,
            "strategy_signal_id": 77,
            "auto_commit": False,
            "auto_match": True,
            "auto_match_mode": "close",
        }
    ]


@pytest.mark.asyncio
async def test_generate_all_signals_records_skipped_order_reason(monkeypatch):
    from app.tasks import signal_tasks

    async def fake_generate_order_from_signal(session, *, user_id, account_id, request, strategy_signal_id=None, auto_commit=True, **_kwargs):
        return {"order": None, "signal": None, "action": "HOLD", "reason": "目标买入金额或可用资金不足一手，未下单"}

    monkeypatch.setattr(signal_tasks, "generate_order_from_signal", fake_generate_order_from_signal)
    session = ScriptedSession(
        [
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '买入', 'target_position': 1, 'current_position': 0}"}]),
            FakeResult([{"ts_code": "000001.SZ"}]),
            FakeResult([]),
            FakeResult([{"id": 5, "user_id": 1}]),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult([{"id": 77}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 1
    assert result["orders_created"] == 0
    assert result["orders_skipped"] == 1
    assert result["order_skip_reasons"] == [
        {
            "strategy_id": 3,
            "account_id": 5,
            "ts_code": "000001.SZ",
            "signal_id": 77,
            "action": "HOLD",
            "reason": "目标买入金额或可用资金不足一手，未下单",
        }
    ]


@pytest.mark.asyncio
async def test_generate_all_signals_uses_saved_concurrency_preference():
    session = ScriptedSession(
        [
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '观望', 'current_position': 0}"}]),
            FakeResult([{"ts_code": "000001.SZ"}]),
            FakeResult([{"value": {"full_kline_sync_concurrency": 4}}]),
            FakeResult([]),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult([{"id": 77}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["concurrency"] == 4
    assert result["signals_logged"] == 1


@pytest.mark.asyncio
async def test_generate_all_signals_prioritizes_sell_then_confidence(monkeypatch):
    from app.tasks import signal_tasks

    calls = []

    async def fake_generate_order_from_signal(session, *, user_id, account_id, request, strategy_signal_id=None, auto_commit=True, **_kwargs):
        calls.append(
            {
                "ts_code": request.ts_code,
                "signal_type": request.signal_type,
                "priority_score": (request.snapshot or {}).get("buy_priority_score"),
                "priority_source": (request.snapshot or {}).get("buy_priority_source"),
            }
        )
        return {"order": {"id": len(calls)}, "signal": None}

    monkeypatch.setattr(signal_tasks, "generate_order_from_signal", fake_generate_order_from_signal)
    source_code = """
def generate_signal(ctx):
    close = ctx.close[-1]
    if close == 10.1:
        return {'signal_type': '买入', 'target_position': 0.5, 'confidence': 0.70}
    if close == 10.2:
        return {'signal_type': '买入', 'target_position': 1.0, 'confidence': 0.90}
    if close == 10.3:
        return {'signal_type': '买入', 'target_position': 1.0}
    return {'signal_type': '卖出', 'target_position': 0}
"""
    session = ScriptedSession(
        [
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": source_code}]),
            FakeResult([
                {"ts_code": "000001.SZ"},
                {"ts_code": "000002.SZ"},
                {"ts_code": "000003.SZ"},
                {"ts_code": "000004.SZ"},
            ]),
            FakeResult([]),
            FakeResult([{"id": 5, "user_id": 1}]),
            FakeResult(batch_kline_rows("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ")),
            FakeResult([]),
            FakeResult([{"id": 77}]),
            FakeResult([]),
            FakeResult([{"id": 78}]),
            FakeResult([]),
            FakeResult([{"id": 79}]),
            FakeResult([]),
            FakeResult([{"id": 80}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 4
    assert result["orders_created"] == 4
    # Sell signal (000004.SZ) first, then buys sorted by confidence descending
    assert [call["ts_code"] for call in calls] == ["000004.SZ", "000002.SZ", "000001.SZ", "000003.SZ"]
    assert calls[1]["priority_source"] == "confidence"
    assert calls[1]["priority_score"] == "0.9"
    assert calls[3]["priority_source"] == "default"
    assert calls[3]["priority_score"] == "0"


@pytest.mark.asyncio
async def test_generate_all_signals_uses_target_position_and_code_as_stable_tiebreakers(monkeypatch):
    from app.tasks import signal_tasks

    calls = []

    async def fake_generate_order_from_signal(session, *, user_id, account_id, request, strategy_signal_id=None, auto_commit=True, **_kwargs):
        calls.append(request.ts_code)
        return {"order": {"id": len(calls)}, "signal": None}

    monkeypatch.setattr(signal_tasks, "generate_order_from_signal", fake_generate_order_from_signal)
    source_code = """
def generate_signal(ctx):
    if ctx.close[-1] == 10.3:
        return {'signal_type': '买入', 'target_position': 0.8, 'confidence': 0.5}
    return {'signal_type': '买入', 'target_position': 0.5, 'confidence': 0.5}
"""
    session = ScriptedSession(
        [
            FakeResult([{"id": 3, "user_id": 1, "name": "S", "source_code": source_code}]),
            FakeResult([
                {"ts_code": "000002.SZ"},
                {"ts_code": "000001.SZ"},
                {"ts_code": "000003.SZ"},
            ]),
            FakeResult([]),
            FakeResult([{"id": 5, "user_id": 1}]),
            FakeResult(batch_kline_rows("000002.SZ", "000001.SZ", "000003.SZ")),
            FakeResult([]),
            FakeResult([{"id": 77}]),
            FakeResult([]),
            FakeResult([{"id": 78}]),
            FakeResult([]),
            FakeResult([{"id": 79}]),
        ]
    )

    result = await generate_all_signals_for_date(session, trade_date=date(2026, 5, 21))

    assert result["signals_logged"] == 3
    assert calls == ["000003.SZ", "000001.SZ", "000002.SZ"]


@pytest.mark.asyncio
async def test_generate_intraday_position_signals_adds_with_ask1(monkeypatch):
    from app.tasks import signal_tasks

    calls = []

    async def fake_fetch_ticks(stock_codes):
        assert stock_codes == ["000001.SZ"]
        return {
            "000001.SZ": RealtimeTick(
                ts_code="000001.SZ",
                price=Decimal("10.00"),
                bid1=Decimal("9.99"),
                ask1=Decimal("10.01"),
            )
        }, None

    async def fake_generate_order_from_signal(session, *, user_id, account_id, request, order_price_override=None, auto_commit=True, allow_missing_kline_with_order_price=False, auto_match=False, auto_match_mode="close", **_kwargs):
        calls.append(
            {
                "user_id": user_id,
                "account_id": account_id,
                "signal_type": request.signal_type,
                "current_position": (request.snapshot or {}).get("current_position"),
                "price": order_price_override,
                "auto_commit": auto_commit,
                "allow_missing_kline_with_order_price": allow_missing_kline_with_order_price,
                "auto_match": auto_match,
                "auto_match_mode": auto_match_mode,
            }
        )
        return {"order": {"id": 9}, "signal": {"id": 8}, "action": "BUY"}

    monkeypatch.setattr(signal_tasks, "_fetch_realtime_ticks", fake_fetch_ticks)
    monkeypatch.setattr(signal_tasks, "generate_order_from_signal", fake_generate_order_from_signal)
    session = ScriptedSession(
        [
            FakeResult([{"is_open": True}]),
            FakeResult([{"id": 5, "user_id": 1, "total_asset": Decimal("100000"), "strategy_id": 3, "strategy_name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '增持', 'target_position': 0.2}"}]),
            FakeResult([{"account_id": 5, "ts_code": "000001.SZ", "shares": 1000, "available_shares": 1000, "market_value": Decimal("9000"), "current_price": Decimal("9")}]),
            FakeResult([]),
            FakeResult(kline_rows()),
        ]
    )

    result = await generate_intraday_position_signals_for_date(
        session,
        trade_date=date(2026, 5, 21),
        now=datetime(2026, 5, 21, 10, 0),
    )

    assert result["signals_logged"] == 1
    assert result["orders_created"] == 1
    assert calls == [
        {
            "user_id": 1,
            "account_id": 5,
            "signal_type": "增持",
            "current_position": "0.1000",
            "price": Decimal("10.01"),
            "auto_commit": False,
            "allow_missing_kline_with_order_price": True,
            "auto_match": True,
            "auto_match_mode": "limit",
        }
    ]


@pytest.mark.asyncio
async def test_generate_intraday_position_signals_reduces_with_bid1(monkeypatch):
    from app.tasks import signal_tasks

    calls = []

    async def fake_fetch_ticks(_stock_codes):
        return {
            "000001.SZ": RealtimeTick(
                ts_code="000001.SZ",
                price=Decimal("10.00"),
                bid1=Decimal("9.98"),
                ask1=Decimal("10.02"),
            )
        }, None

    async def fake_generate_order_from_signal(session, *, request, order_price_override=None, **_kwargs):
        calls.append({"signal_type": request.signal_type, "price": order_price_override})
        return {"order": {"id": 9}, "signal": {"id": 8}, "action": "SELL_PARTIAL"}

    monkeypatch.setattr(signal_tasks, "_fetch_realtime_ticks", fake_fetch_ticks)
    monkeypatch.setattr(signal_tasks, "generate_order_from_signal", fake_generate_order_from_signal)
    session = ScriptedSession(
        [
            FakeResult([{"is_open": True}]),
            FakeResult([{"id": 5, "user_id": 1, "total_asset": Decimal("100000"), "strategy_id": 3, "strategy_name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '减仓', 'target_position': 0.05}"}]),
            FakeResult([{"account_id": 5, "ts_code": "000001.SZ", "shares": 1000, "available_shares": 1000, "market_value": Decimal("9000"), "current_price": Decimal("9")}]),
            FakeResult([]),
            FakeResult(kline_rows()),
        ]
    )

    result = await generate_intraday_position_signals_for_date(
        session,
        trade_date=date(2026, 5, 21),
        now=datetime(2026, 5, 21, 10, 0),
    )

    assert result["orders_created"] == 1
    assert calls == [{"signal_type": "减仓", "price": Decimal("9.98")}]


@pytest.mark.asyncio
async def test_generate_intraday_position_signals_skips_buy_and_pending_order(monkeypatch):
    from app.tasks import signal_tasks

    calls = []

    async def fake_fetch_ticks(_stock_codes):
        return {
            "000001.SZ": RealtimeTick(ts_code="000001.SZ", price=Decimal("10.00")),
            "000002.SZ": RealtimeTick(ts_code="000002.SZ", price=Decimal("20.00")),
        }, None

    async def fake_generate_order_from_signal(*_args, **_kwargs):
        calls.append(_kwargs)
        return {"order": {"id": 9}}

    monkeypatch.setattr(signal_tasks, "_fetch_realtime_ticks", fake_fetch_ticks)
    monkeypatch.setattr(signal_tasks, "generate_order_from_signal", fake_generate_order_from_signal)
    session = ScriptedSession(
        [
            FakeResult([{"is_open": True}]),
            FakeResult([{"id": 5, "user_id": 1, "total_asset": Decimal("100000"), "strategy_id": 3, "strategy_name": "S", "source_code": "def generate_signal(ctx):\n    return {'signal_type': '买入', 'target_position': 0.5}"}]),
            FakeResult([
                {"account_id": 5, "ts_code": "000001.SZ", "shares": 1000, "available_shares": 1000},
                {"account_id": 5, "ts_code": "000002.SZ", "shares": 1000, "available_shares": 1000},
            ]),
            FakeResult([{"account_id": 5, "ts_code": "000002.SZ"}]),
            FakeResult(kline_rows()),
        ]
    )

    result = await generate_intraday_position_signals_for_date(
        session,
        trade_date=date(2026, 5, 21),
        now=datetime(2026, 5, 21, 10, 0),
    )

    assert result["orders_created"] == 0
    assert result["orders_skipped"] == 2
    assert calls == []
    assert result["order_skip_reasons"] == [
        {"account_id": 5, "ts_code": "000001.SZ", "reason": "盘中持仓调仓忽略买入信号"},
        {"account_id": 5, "ts_code": "000002.SZ", "reason": "已有待成交委托"},
    ]


@pytest.mark.asyncio
async def test_generate_intraday_position_signals_skips_outside_trading_window():
    session = ScriptedSession([FakeResult([{"is_open": True}])])

    result = await generate_intraday_position_signals_for_date(
        session,
        trade_date=date(2026, 5, 21),
        now=datetime(2026, 5, 21, 12, 0),
    )

    assert result == {
        "trade_date": "2026-05-21",
        "skipped": True,
        "reason": "outside intraday trading hours",
    }
