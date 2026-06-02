from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.data.providers import DataProviderError
from app.db.session import get_session
from app.main import app
from app.realtime.models import RealtimeTick


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
        response = client.get("/api/signals?signal_type=买入&page_size=10")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["summary"]["buy_count"] == 1
    assert body["items"][0]["target_position"] == "1.000000"
    assert "sl.account_id IS NULL" in fake_session.statements[0]
    assert "sl.account_id IS NULL" in fake_session.statements[1]
    assert "sl.account_id IS NULL" in fake_session.statements[2]
    assert "sl.account_id = :account_id" not in fake_session.statements[0]
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


def _patch_account_fake_session():
    existing_row = {
        "id": 1,
        "user_id": 1,
        "strategy_id": 7,
        "name": "M4",
        "initial_cash": Decimal("100000.0000"),
        "available_cash": Decimal("100000.0000"),
        "frozen_cash": Decimal("0.0000"),
        "total_asset": Decimal("100000.0000"),
        "status": "active",
        "config": {"risk_config": {"max_position_pct": 0.5}, "fee_config": {"commission_rate": 0.00025}},
        "created_at": date(2026, 5, 21),
        "updated_at": date(2026, 5, 21),
        "user_trading_fee_config": None,
    }
    updated_row = {
        **existing_row,
        "updated_at": date(2026, 5, 22),
    }
    return FakeSession([FakeResult([existing_row]), FakeResult([updated_row])])


def _patch_account(body):
    fake_session = _patch_account_fake_session()

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.patch("/api/sim/accounts/1", json=body)
    finally:
        app.dependency_overrides.clear()

    return response, fake_session


def test_patch_sim_account_config_keeps_bound_strategy_when_strategy_id_missing():
    response, fake_session = _patch_account({"config": {"risk_config": {"max_position_pct": 0.8}}})

    assert response.status_code == 200
    assert fake_session.params[1]["strategy_id"] == 7
    assert fake_session.params[1]["name"] == "M4"
    assert fake_session.params[1]["config"] == (
        '{"risk_config": {"max_position_pct": 0.8}, "fee_config": {"commission_rate": 0.00025}}'
    )
    assert fake_session.commits == 1


def test_patch_sim_account_strategy_id_null_unbinds_strategy():
    response, fake_session = _patch_account({"strategy_id": None})

    assert response.status_code == 200
    assert fake_session.params[1]["strategy_id"] is None
    assert fake_session.params[1]["config"] is None


def test_patch_sim_account_strategy_id_binds_strategy():
    response, fake_session = _patch_account({"strategy_id": 2})

    assert response.status_code == 200
    assert fake_session.params[1]["strategy_id"] == 2
    assert fake_session.params[1]["config"] is None


def test_patch_sim_account_strategy_id_and_config_update_together():
    response, fake_session = _patch_account(
        {"strategy_id": 2, "config": {"risk_config": {"stop_loss_pct": 0.08}}}
    )

    assert response.status_code == 200
    assert fake_session.params[1]["strategy_id"] == 2
    assert fake_session.params[1]["config"] == (
        '{"risk_config": {"stop_loss_pct": 0.08}, "fee_config": {"commission_rate": 0.00025}}'
    )


def test_sim_account_list_uses_realtime_prices_for_total_asset(monkeypatch):
    from app.sim import service as sim_service

    async def fake_snapshot(self):
        return [
            RealtimeTick(
                ts_code="000001.SZ",
                price=Decimal("12.0000"),
                change=Decimal("1.0000"),
                change_pct=Decimal("9.0909"),
            )
        ]

    monkeypatch.setattr(sim_service.EastMoneyRealtimeProvider, "fetch_snapshot", fake_snapshot)
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
                        "available_cash": Decimal("1000.0000"),
                        "frozen_cash": Decimal("200.0000"),
                        "total_asset": Decimal("11200.0000"),
                        "status": "active",
                        "config": {},
                        "created_at": date(2026, 5, 21),
                        "updated_at": date(2026, 5, 21),
                        "strategy_name": None,
                    }
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 9,
                        "account_id": 1,
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "shares": 1000,
                        "available_shares": 1000,
                        "frozen_shares": 0,
                        "avg_cost": Decimal("10.0000"),
                        "current_price": Decimal("10.0000"),
                        "market_value": Decimal("10000.0000"),
                        "unrealized_pnl": Decimal("0.0000"),
                        "profit_rate": Decimal("0.00000000"),
                        "updated_at": date(2026, 5, 21),
                    }
                ]
            ),
            FakeResult([{"ts_code": "000001.SZ", "baseline_price": Decimal("11.0000")}]),
            FakeResult([{"total_asset": Decimal("12000.0000")}]),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/sim/accounts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["position_value"] == "12000.0000"
    assert body[0]["unrealized_pnl"] == "2000.0000"
    assert body[0]["today_pnl"] == "1200.0000"
    assert body[0]["today_pnl_rate"] == "0.10000000"
    assert body[0]["total_asset"] == "13200.0000"
    assert body[0]["valuation_source"] == "realtime"
    assert body[0]["positions"][0]["stock_name"] == "平安银行"
    assert body[0]["positions"][0]["current_price"] == "12.0000"
    assert body[0]["positions"][0]["market_value"] == "12000.0000"
    assert body[0]["positions"][0]["unrealized_pnl"] == "2000.0000"
    assert body[0]["positions"][0]["profit_rate"] == "0.20000000"
    assert body[0]["positions"][0]["today_pnl"] == "1000.0000"
    assert body[0]["positions"][0]["today_pnl_rate"] == "0.09090900"


def test_sim_account_list_sorts_by_realtime_total_asset_desc(monkeypatch):
    from app.sim import service as sim_service

    async def fake_snapshot(self):
        return [
            RealtimeTick(ts_code="000001.SZ", price=Decimal("12.0000")),
            RealtimeTick(ts_code="600000.SH", price=Decimal("20.0000")),
        ]

    monkeypatch.setattr(sim_service.EastMoneyRealtimeProvider, "fetch_snapshot", fake_snapshot)
    fake_session = FakeSession(
        [
            FakeResult(
                [
                    {
                        "id": 1,
                        "user_id": 1,
                        "strategy_id": None,
                        "name": "Low",
                        "initial_cash": Decimal("100000.0000"),
                        "available_cash": Decimal("1000.0000"),
                        "frozen_cash": Decimal("0.0000"),
                        "total_asset": Decimal("500000.0000"),
                        "status": "active",
                        "config": {},
                        "created_at": date(2026, 5, 21),
                        "updated_at": date(2026, 5, 21),
                        "strategy_name": None,
                    },
                    {
                        "id": 2,
                        "user_id": 1,
                        "strategy_id": None,
                        "name": "High",
                        "initial_cash": Decimal("100000.0000"),
                        "available_cash": Decimal("2000.0000"),
                        "frozen_cash": Decimal("0.0000"),
                        "total_asset": Decimal("100000.0000"),
                        "status": "active",
                        "config": {},
                        "created_at": date(2026, 5, 21),
                        "updated_at": date(2026, 5, 21),
                        "strategy_name": None,
                    },
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 9,
                        "account_id": 1,
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "shares": 1000,
                        "available_shares": 1000,
                        "frozen_shares": 0,
                        "avg_cost": Decimal("10.0000"),
                        "current_price": Decimal("10.0000"),
                        "market_value": Decimal("10000.0000"),
                        "unrealized_pnl": Decimal("0.0000"),
                        "profit_rate": Decimal("0.00000000"),
                        "updated_at": date(2026, 5, 21),
                    }
                ]
            ),
            FakeResult([{"ts_code": "000001.SZ", "baseline_price": Decimal("11.0000")}]),
            FakeResult([]),
            FakeResult(
                [
                    {
                        "id": 10,
                        "account_id": 2,
                        "ts_code": "600000.SH",
                        "stock_name": "浦发银行",
                        "shares": 1000,
                        "available_shares": 1000,
                        "frozen_shares": 0,
                        "avg_cost": Decimal("10.0000"),
                        "current_price": Decimal("10.0000"),
                        "market_value": Decimal("10000.0000"),
                        "unrealized_pnl": Decimal("0.0000"),
                        "profit_rate": Decimal("0.00000000"),
                        "updated_at": date(2026, 5, 21),
                    }
                ]
            ),
            FakeResult([{"ts_code": "600000.SH", "baseline_price": Decimal("19.0000")}]),
            FakeResult([]),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/sim/accounts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert [account["name"] for account in body] == ["High", "Low"]
    assert [account["total_asset"] for account in body] == ["22000.0000", "13000.0000"]
    assert "ORDER BY a.total_asset DESC, a.id DESC" in fake_session.statements[0]


def test_sim_positions_endpoint_uses_realtime_prices(monkeypatch):
    from app.sim import service as sim_service

    async def fake_snapshot(self):
        return [
            RealtimeTick(
                ts_code="000001.SZ",
                price=Decimal("8.5000"),
                change=Decimal("-0.5000"),
                change_pct=Decimal("-5.5556"),
            )
        ]

    monkeypatch.setattr(sim_service.EastMoneyRealtimeProvider, "fetch_snapshot", fake_snapshot)
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
                        "available_cash": Decimal("1000.0000"),
                        "frozen_cash": Decimal("0.0000"),
                        "total_asset": Decimal("11000.0000"),
                        "status": "active",
                        "config": {},
                        "created_at": date(2026, 5, 21),
                        "updated_at": date(2026, 5, 21),
                        "user_trading_fee_config": None,
                    }
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 9,
                        "account_id": 1,
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "shares": 1000,
                        "available_shares": 1000,
                        "frozen_shares": 0,
                        "avg_cost": Decimal("10.0000"),
                        "current_price": Decimal("10.0000"),
                        "market_value": Decimal("10000.0000"),
                        "unrealized_pnl": Decimal("0.0000"),
                        "profit_rate": Decimal("0.00000000"),
                        "updated_at": date(2026, 5, 21),
                    }
                ]
            ),
            FakeResult([{"ts_code": "000001.SZ", "baseline_price": Decimal("8.0000")}]),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/sim/accounts/1/positions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["stock_name"] == "平安银行"
    assert body[0]["current_price"] == "8.5000"
    assert body[0]["market_value"] == "8500.0000"
    assert body[0]["unrealized_pnl"] == "-1500.0000"
    assert body[0]["profit_rate"] == "-0.15000000"
    assert body[0]["today_pnl"] == "-500.0000"
    assert body[0]["today_pnl_rate"] == "-0.05555600"
    assert body[0]["valuation_source"] == "realtime"
    assert "current_positions" in fake_session.statements[1]
    assert "COALESCE(pre_close, close) AS baseline_price" in fake_session.statements[2]


def test_sim_positions_endpoint_falls_back_to_kline_baseline_without_realtime_tick(monkeypatch):
    from app.sim import service as sim_service

    async def fake_snapshot(self):
        return []

    monkeypatch.setattr(sim_service.EastMoneyRealtimeProvider, "fetch_snapshot", fake_snapshot)
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
                        "available_cash": Decimal("1000.0000"),
                        "frozen_cash": Decimal("0.0000"),
                        "total_asset": Decimal("11000.0000"),
                        "status": "active",
                        "config": {},
                        "created_at": date(2026, 5, 21),
                        "updated_at": date(2026, 5, 21),
                        "user_trading_fee_config": None,
                    }
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 9,
                        "account_id": 1,
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "shares": 1000,
                        "available_shares": 1000,
                        "frozen_shares": 0,
                        "avg_cost": Decimal("10.0000"),
                        "current_price": Decimal("8.5000"),
                        "market_value": Decimal("8500.0000"),
                        "unrealized_pnl": Decimal("-1500.0000"),
                        "profit_rate": Decimal("-0.15000000"),
                        "updated_at": date(2026, 5, 21),
                    }
                ]
            ),
            FakeResult([{"ts_code": "000001.SZ", "baseline_price": Decimal("9.0000")}]),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/sim/accounts/1/positions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["today_pnl"] == "-500.0000"
    assert body[0]["today_pnl_rate"] == "-0.05555556"


def test_sim_positions_endpoint_defaults_today_pnl_without_latest_close(monkeypatch):
    from app.sim import service as sim_service

    async def fake_snapshot(self):
        return [RealtimeTick(ts_code="000001.SZ", price=Decimal("8.5000"))]

    monkeypatch.setattr(sim_service.EastMoneyRealtimeProvider, "fetch_snapshot", fake_snapshot)
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
                        "available_cash": Decimal("1000.0000"),
                        "frozen_cash": Decimal("0.0000"),
                        "total_asset": Decimal("11000.0000"),
                        "status": "active",
                        "config": {},
                        "created_at": date(2026, 5, 21),
                        "updated_at": date(2026, 5, 21),
                        "user_trading_fee_config": None,
                    }
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 9,
                        "account_id": 1,
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "shares": 1000,
                        "available_shares": 1000,
                        "frozen_shares": 0,
                        "avg_cost": Decimal("10.0000"),
                        "current_price": Decimal("10.0000"),
                        "market_value": Decimal("10000.0000"),
                        "unrealized_pnl": Decimal("0.0000"),
                        "profit_rate": Decimal("0.00000000"),
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
        response = client.get("/api/sim/accounts/1/positions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["today_pnl"] == "0.0000"
    assert body[0]["today_pnl_rate"] == "0.00000000"


def test_sim_positions_endpoint_hides_previous_day_cleared_positions(monkeypatch):
    from app.sim import service as sim_service

    async def fake_snapshot(self):
        return []

    monkeypatch.setattr(sim_service.EastMoneyRealtimeProvider, "fetch_snapshot", fake_snapshot)
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
                        "user_trading_fee_config": None,
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
        response = client.get("/api/sim/accounts/1/positions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []
    assert "today_traded" in fake_session.statements[1]


def test_sim_positions_endpoint_shows_today_cleared_positions(monkeypatch):
    from app.sim import service as sim_service

    async def fake_snapshot(self):
        return [
            RealtimeTick(
                ts_code="000001.SZ",
                price=Decimal("8.5000"),
                change=Decimal("-0.5000"),
                change_pct=Decimal("-5.5556"),
            )
        ]

    monkeypatch.setattr(sim_service.EastMoneyRealtimeProvider, "fetch_snapshot", fake_snapshot)
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
                        "user_trading_fee_config": None,
                    }
                ]
            ),
            FakeResult([
                {
                    "id": 9,
                    "account_id": 1,
                    "ts_code": "000001.SZ",
                    "stock_name": "平安银行",
                    "shares": 0,
                    "available_shares": 0,
                    "frozen_shares": 0,
                    "avg_cost": Decimal("10.0000"),
                    "current_price": Decimal("8.5000"),
                    "market_value": Decimal("0.0000"),
                    "unrealized_pnl": Decimal("-1500.0000"),
                    "profit_rate": Decimal("-0.15000000"),
                    "first_buy_date": None,
                    "updated_at": date(2026, 5, 21),
                    "closed_today": True,
                }
            ]),
            FakeResult([{"ts_code": "000001.SZ", "baseline_price": Decimal("9.0000")}]),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/sim/accounts/1/positions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["stock_name"] == "平安银行"
    assert body[0]["shares"] == 0
    assert body[0]["closed_today"] is True
    assert body[0]["avg_cost"] == "10.0000"
    assert body[0]["market_value"] == "0.0000"
    assert body[0]["unrealized_pnl"] == "-1500.0000"
    assert body[0]["profit_rate"] == "-0.15000000"
    assert body[0]["today_pnl"] == "0.0000"
    assert body[0]["today_pnl_rate"] == "-0.05555600"
    assert "p.shares = 0 AND tt.sold_shares > 0" in fake_session.statements[1]


def test_sim_account_list_falls_back_to_stored_valuation_on_realtime_error(monkeypatch):
    from app.sim import service as sim_service

    async def fake_snapshot(self):
        raise DataProviderError("行情接口超时")

    monkeypatch.setattr(sim_service.EastMoneyRealtimeProvider, "fetch_snapshot", fake_snapshot)
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
                        "available_cash": Decimal("1000.0000"),
                        "frozen_cash": Decimal("200.0000"),
                        "total_asset": Decimal("11200.0000"),
                        "status": "active",
                        "config": {},
                        "created_at": date(2026, 5, 21),
                        "updated_at": date(2026, 5, 21),
                        "strategy_name": None,
                    }
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 9,
                        "account_id": 1,
                        "ts_code": "000001.SZ",
                        "stock_name": None,
                        "shares": 1000,
                        "available_shares": 1000,
                        "frozen_shares": 0,
                        "avg_cost": Decimal("10.0000"),
                        "current_price": Decimal("11.0000"),
                        "market_value": Decimal("11000.0000"),
                        "unrealized_pnl": Decimal("1000.0000"),
                        "profit_rate": Decimal("0.10000000"),
                        "updated_at": date(2026, 5, 21),
                    }
                ]
            ),
            FakeResult([]),
            FakeResult([{"total_asset": Decimal("12200.0000")}]),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/sim/accounts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["position_value"] == "11000.0000"
    assert body[0]["unrealized_pnl"] == "1000.0000"
    assert body[0]["today_pnl"] == "0.0000"
    assert body[0]["today_pnl_rate"] == "0.00000000"
    assert body[0]["total_asset"] == "12200.0000"
    assert body[0]["valuation_source"] == "stored"
    assert body[0]["valuation_error"] == "行情接口超时"
    assert body[0]["positions"][0]["stock_name"] == "000001.SZ"


def test_delete_sim_account_deletes_owned_account():
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
                        "user_trading_fee_config": None,
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
        response = client.delete("/api/sim/accounts/1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert "DELETE FROM sim_accounts" in fake_session.statements[1]
    assert fake_session.params[1] == {"id": 1, "user_id": 1}
    assert fake_session.commits == 1


def test_sim_orders_endpoint_includes_stock_name():
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
                        "user_trading_fee_config": None,
                    }
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 9,
                        "account_id": 1,
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "direction": "买入",
                        "price": Decimal("10.0000"),
                        "volume": 100,
                        "filled_volume": 0,
                        "frozen_amount": Decimal("1000.0000"),
                        "status": "待成交",
                        "reject_reason": None,
                        "submit_time": date(2026, 5, 21),
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
        response = client.get("/api/sim/accounts/1/orders")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["stock_name"] == "平安银行"
    assert "LEFT JOIN stock_basic" in fake_session.statements[1]
    assert "ORDER BY t.submit_time DESC, t.id DESC" in fake_session.statements[1]


def test_sim_trades_endpoint_includes_stock_name():
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
                        "user_trading_fee_config": None,
                    }
                ]
            ),
            FakeResult(
                [
                    {
                        "id": 11,
                        "account_id": 1,
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "direction": "买入",
                        "price": Decimal("10.0000"),
                        "volume": 100,
                        "amount": Decimal("1000.0000"),
                        "total_fee": Decimal("5.0000"),
                        "trade_time": date(2026, 5, 21),
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
        response = client.get("/api/sim/accounts/1/trades")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["stock_name"] == "平安银行"
    assert "LEFT JOIN stock_basic" in fake_session.statements[1]
    assert "ORDER BY t.trade_time DESC, t.id DESC" in fake_session.statements[1]


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
