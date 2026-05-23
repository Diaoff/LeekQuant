from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app


class FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar if self._scalar is not None else self._rows[0]


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.params = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        return self.results.pop(0) if self.results else FakeResult([])


def test_factors_api_returns_definitions():
    fake_session = FakeSession(
        [
            FakeResult(
                [
                    {
                        "name": "roe",
                        "display_name": "ROE",
                        "category": "quality",
                        "expression": "stock_fundamentals.roe",
                        "direction": 1,
                        "default_weight": Decimal("1.200000"),
                        "enabled": True,
                        "description": "净资产收益率",
                        "created_at": date(2026, 5, 22),
                        "updated_at": date(2026, 5, 22),
                    }
                ]
            )
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/factors?enabled_only=true")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["name"] == "roe"
    assert body[0]["default_weight"] == "1.200000"
    assert "enabled = TRUE" in fake_session.statements[0]


def test_factor_rank_api_returns_paginated_shape():
    fake_session = FakeSession(
        [
            FakeResult(scalar=1),
            FakeResult(
                [
                    {
                        "id": 1,
                        "trade_date": date(2026, 5, 22),
                        "ts_code": "000001.SZ",
                        "stock_name": "平安银行",
                        "scope_type": "all",
                        "scope_value": None,
                        "total_score": Decimal("0.90000000"),
                        "rank": 1,
                        "percentile_rank": Decimal("1.00000000"),
                        "factor_breakdown": {"roe": {"weight": "1.2"}},
                        "created_at": date(2026, 5, 22),
                        "updated_at": date(2026, 5, 22),
                    }
                ]
            ),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/factors/rank?trade_date=2026-05-22&page_size=2")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page_size"] == 2
    assert body["items"][0]["total_score"] == "0.90000000"
    assert fake_session.params[1]["limit"] == 2


def test_factor_rank_api_rejects_watchlist_scope_without_group():
    fake_session = FakeSession([])

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/factors/rank?scope_type=watchlist_group")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert fake_session.statements == []


def test_factor_analysis_api_returns_stable_shape():
    fake_session = FakeSession(
        [
            FakeResult(scalar=1),
            FakeResult(
                [
                    {
                        "id": 1,
                        "factor_name": "roe",
                        "display_name": "ROE",
                        "period_start": date(2026, 5, 1),
                        "period_end": date(2026, 5, 22),
                        "forward_days": 5,
                        "ic": Decimal("0.10000000"),
                        "ic_mean": Decimal("0.08000000"),
                        "ic_std": Decimal("0.02000000"),
                        "ir": Decimal("4.00000000"),
                        "icir": Decimal("4.00000000"),
                        "ic_gt_0_pct": Decimal("1.00000000"),
                        "group_returns": {},
                        "details": {"ic_by_date": []},
                        "created_at": date(2026, 5, 22),
                        "updated_at": date(2026, 5, 22),
                    }
                ]
            ),
        ]
    )

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/factors/analysis?factor_name=roe")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["items"][0]["ir"] == "4.00000000"
    assert "fa.factor_name = :factor_name" in fake_session.statements[0]


def test_factor_values_api_requires_date_and_factor_name():
    fake_session = FakeSession([FakeResult(scalar=0), FakeResult([])])

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/factors/values?trade_date=2026-05-22&factor_name=roe")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert fake_session.params[0]["factor_name"] == "roe"
