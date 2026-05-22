from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app


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

    def scalar_one(self):
        return self._scalar if self._scalar is not None else self._rows[0]


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.params = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        return self.results.pop(0) if self.results else FakeResult([])

    async def commit(self):
        self.commits += 1


def test_signals_api_returns_paginated_stable_shape():
    fake_session = FakeSession(
        [
            FakeResult(scalar=1),
            FakeResult(
                [
                    {
                        "buy_count": 1,
                        "add_count": 0,
                        "reduce_count": 0,
                        "sell_count": 0,
                        "hold_count": 0,
                        "blocked_count": 0,
                    }
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 1,
                        "user_id": 1,
                        "strategy_id": 2,
                        "strategy_name": "策略",
                        "account_id": 3,
                        "account_name": "模拟",
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "trade_date": date(2026, 5, 21),
                        "signal_type": "买入",
                        "target_position": Decimal("1.000000"),
                        "current_position": Decimal("0.000000"),
                        "action": "BUY",
                        "confidence": Decimal("0.900000"),
                        "reason": "test",
                        "snapshot": {"close": 10},
                        "created_at": date(2026, 5, 21),
                    }
                ]
            ),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/signals?account_id=3&signal_type=买入&page_size=10")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["summary"]["buy_count"] == 1
    assert body["items"][0]["target_position"] == "1.000000"
    assert "sl.account_id = :account_id" in fake_session.statements[0]
    assert fake_session.params[2]["limit"] == 10


def test_create_sim_account_initializes_cash_fields():
    fake_session = FakeSession(
        [
            FakeResult(
                [
                    {
                        "id": 1,
                        "user_id": 1,
                        "strategy_id": None,
                        "name": "M4",
                        "initial_cash": Decimal("100000.0000"),
                        "available_cash": Decimal("100000.0000"),
                        "frozen_cash": Decimal("0.0000"),
                        "total_asset": Decimal("100000.0000"),
                        "status": "active",
                        "config": {},
                        "created_at": date(2026, 5, 21),
                        "updated_at": date(2026, 5, 21),
                    }
                ]
            ),
            FakeResult([]),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.post("/api/sim/accounts", json={"name": "M4", "initial_cash": "100000"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["available_cash"] == "100000.0000"
    assert body["total_asset"] == "100000.0000"
    assert fake_session.params[0]["initial_cash"] == Decimal("100000.0000")
    assert fake_session.commits == 1


def test_match_order_api_passes_match_mode():
    fake_session = FakeSession(
        [
            FakeResult(
                [
                    {
                        "id": 9,
                        "account_id": 1,
                        "signal_id": 7,
                        "ts_code": "000001.SZ",
                        "direction": "买入",
                        "order_type": "限价",
                        "price": Decimal("10.0000"),
                        "volume": 100,
                        "filled_volume": 0,
                        "frozen_amount": Decimal("1002.5000"),
                        "status": "待成交",
                        "reject_reason": None,
                        "submit_time": date(2026, 5, 21),
                        "update_time": date(2026, 5, 21),
                        "cancel_time": None,
                        "user_id": 1,
                        "config": {},
                    }
                ]
            ),
            FakeResult([{"cal_date": date(2026, 5, 21), "is_open": True, "pretrade_date": date(2026, 5, 20), "nexttrade_date": date(2026, 5, 22)}]),
            FakeResult([
                {
                    "ts_code": "000001.SZ",
                    "trade_date": date(2026, 5, 21),
                    "open": Decimal("9.5000"),
                    "high": Decimal("10.2000"),
                    "low": Decimal("9.9000"),
                    "close": Decimal("10.0000"),
                    "pre_close": Decimal("9.8000"),
                    "is_suspended": False,
                    "is_limit_up": False,
                    "is_limit_down": False,
                }
            ]),
            FakeResult([
                {
                    "id": 11,
                    "order_id": 9,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "direction": "买入",
                    "price": Decimal("9.5000"),
                    "volume": 100,
                    "amount": Decimal("950.0000"),
                    "stamp_tax": Decimal("0.0000"),
                    "commission": Decimal("5.0000"),
                    "transfer_fee": Decimal("0.0095"),
                    "total_fee": Decimal("5.0095"),
                    "trade_time": date(2026, 5, 21),
                }
            ]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.post("/api/sim/orders/9/match", json={"trade_date": "2026-05-21", "match_mode": "open"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_session.params[3]["price"] == Decimal("9.5000")
