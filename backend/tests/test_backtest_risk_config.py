"""Regression tests for per-run backtest risk controls."""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.api import backtests
from app.backtest.tasks import _has_risk_controls, _merge_backtest_config, _stock_scope_diagnostics


class FakeResult:
    def __init__(self, rows=None, rowcount=1):
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one(self):
        return self._rows[0]

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class CaptureSession:
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


class FakeRequest:
    headers = {"X-User-ID": "1"}
    query_params = {}


@pytest.mark.asyncio
async def test_submit_backtest_persists_run_config_in_params_snapshot(monkeypatch) -> None:
    submitted = {}

    def fake_apply_async(**kwargs):
        submitted.update(kwargs)

    monkeypatch.setattr(backtests.run_backtest_task, "apply_async", fake_apply_async)
    monkeypatch.setattr(backtests, "uuid4", lambda: type("FixedUuid", (), {"hex": "task-1"})())

    session = CaptureSession(
        [
            FakeResult([{"id": 3, "user_id": 1}]),
            FakeResult([{"id": 11, "strategy_id": 3, "status": "pending", "created_at": date(2026, 5, 20)}]),
        ]
    )
    request = backtests.BacktestCreateRequest(
        strategy_id=3,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 1),
        initial_cash=100000,
        config={"stop_loss_pct": 0.05, "take_profit_pct": 0.1},
        target_type="market",
        target_value="创业板",
    )

    response = await backtests.submit_backtest(FakeRequest(), request, session)

    params = json.loads(session.params[1]["params"])
    assert params["config"] == {"stop_loss_pct": 0.05, "take_profit_pct": 0.1}
    assert params["start_date"] == "2026-01-01"
    assert params["target"] == {"type": "market", "value": "创业板", "label": "创业板"}
    assert response["task_id"] == "task-1"
    assert submitted == {"kwargs": {"backtest_id": 11}, "task_id": "task-1"}


def test_merge_backtest_config_promotes_flat_run_risk_fields() -> None:
    merged = _merge_backtest_config(
        {"risk_config": {"stop_loss_pct": 0.2}, "fee_config": {"commission_rate": "0.00025"}},
        {
            "config": {
                "stop_loss_pct": 0.05,
                "take_profit_pct": 0.1,
                "time_stop_days": 5,
            }
        },
    )

    assert merged["fee_config"] == {"commission_rate": "0.00025"}
    assert merged["risk_config"] == {
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.1,
        "time_stop_days": 5,
    }
    assert _has_risk_controls(merged["risk_config"]) is True


def test_merge_backtest_config_accepts_nested_run_risk_config_json() -> None:
    merged = _merge_backtest_config(
        '{"risk_config": {"stop_loss_pct": 0.2}}',
        json.dumps({"config": {"risk_config": {"trailing_stop_pct": 0.03}}}),
    )

    assert merged["risk_config"] == {
        "stop_loss_pct": 0.2,
        "trailing_stop_pct": 0.03,
    }
    assert _has_risk_controls(merged["risk_config"]) is True


def test_stock_scope_diagnostics_reports_stock_count() -> None:
    assert _stock_scope_diagnostics(["000001.SZ", "002001.SZ"]) == {"stock_count": 2}
