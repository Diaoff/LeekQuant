"""Regression tests for per-run backtest risk controls."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from app.api import backtests
from app.backtest.cost import FeeConfig
from app.backtest.tasks import (
    _filters_from_snapshot,
    _has_risk_controls,
    _merge_fee_config,
    _merge_backtest_config,
    _resolve_stock_codes,
    _stock_scope_diagnostics,
    _target_from_snapshot,
)


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

    def all(self):
        return self._rows


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
    assert params["filters"] == {"exclude_st": True, "exclude_loss_pe": True}
    assert params["target"] == {"type": "market", "value": ["创业板"], "label": "创业板"}
    assert response["task_id"] == "task-1"
    assert submitted == {"kwargs": {"backtest_id": 11}, "task_id": "task-1"}


@pytest.mark.asyncio
async def test_submit_backtest_persists_multiple_market_targets(monkeypatch) -> None:
    monkeypatch.setattr(backtests.run_backtest_task, "apply_async", lambda **_kwargs: None)
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
        target_type="market",
        target_value=["科创板", "主板", "主板"],
    )

    await backtests.submit_backtest(FakeRequest(), request, session)

    params = json.loads(session.params[1]["params"])
    assert params["target"] == {"type": "market", "value": ["主板", "科创板"], "label": "主板、科创板"}


def test_backtest_filter_defaults_follow_target_type() -> None:
    market_request = backtests.BacktestCreateRequest(
        strategy_id=3,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 1),
        target_type="all",
    )
    assert market_request.exclude_st is True
    assert market_request.exclude_loss_pe is True

    watchlist_request = backtests.BacktestCreateRequest(
        strategy_id=3,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 1),
        target_type="watchlist_group",
        target_value="核心观察",
    )
    assert watchlist_request.exclude_st is False
    assert watchlist_request.exclude_loss_pe is False


def test_backtest_filter_explicit_values_override_defaults() -> None:
    request = backtests.BacktestCreateRequest(
        strategy_id=3,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 1),
        target_type="market",
        target_value="主板",
        exclude_st=False,
        exclude_loss_pe=False,
    )

    assert request.exclude_st is False
    assert request.exclude_loss_pe is False


def test_market_target_accepts_old_string_and_rejects_empty_or_invalid_values() -> None:
    request = backtests.BacktestCreateRequest(
        strategy_id=3,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 1),
        target_type="market",
        target_value="创业板",
    )
    assert request.target_value == ["创业板"]

    with pytest.raises(ValueError):
        backtests.BacktestCreateRequest(
            strategy_id=3,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
            target_type="market",
            target_value=[],
        )

    with pytest.raises(ValueError):
        backtests.BacktestCreateRequest(
            strategy_id=3,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
            target_type="market",
            target_value=["港股"],
        )

    with pytest.raises(ValueError):
        backtests.BacktestCreateRequest(
            strategy_id=3,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
            target_type="market",
            target_value=["主板", "港股"],
        )


def test_filters_from_snapshot_defaults_and_explicit_values() -> None:
    assert _filters_from_snapshot({"target": {"type": "all", "value": None}}) == {
        "exclude_st": True,
        "exclude_loss_pe": True,
    }
    assert _filters_from_snapshot({"target": {"type": "watchlist_group", "value": "A"}}) == {
        "exclude_st": False,
        "exclude_loss_pe": False,
    }
    assert _filters_from_snapshot(
        {
            "target": {"type": "watchlist_group", "value": "A"},
            "filters": {"exclude_st": True, "exclude_loss_pe": True},
        }
    ) == {"exclude_st": True, "exclude_loss_pe": True}


def test_target_from_snapshot_accepts_old_string_and_new_market_arrays() -> None:
    assert _target_from_snapshot({"target": {"type": "market", "value": "创业板"}}) == {
        "type": "market",
        "value": ["创业板"],
    }
    assert _target_from_snapshot({"target": {"type": "market", "value": ["科创板", "主板"]}}) == {
        "type": "market",
        "value": ["主板", "科创板"],
    }
    assert _target_from_snapshot({"target": {"type": "market", "value": ["港股"]}}) == {
        "type": "all",
        "value": None,
    }


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


def test_merge_backtest_config_merges_fee_config_by_field() -> None:
    merged = _merge_backtest_config(
        {"fee_config": {"commission_rate": "0.0003", "min_commission": "8"}},
        {"config": {"fee_config": {"waive_min_commission": True}}},
    )

    assert merged["fee_config"] == {
        "commission_rate": "0.0003",
        "min_commission": "8",
        "waive_min_commission": True,
    }


def test_merge_fee_config_applies_local_over_global_over_defaults() -> None:
    merged = _merge_fee_config(
        FeeConfig(waive_min_commission=True),
        {"commission_rate": "0.0003"},
    )

    assert merged.commission_rate == Decimal("0.0003")
    assert merged.min_commission == Decimal("5.0")
    assert merged.stamp_tax_rate == Decimal("0.0005")
    assert merged.transfer_fee_rate == Decimal("0.00001")
    assert merged.waive_min_commission is True


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


@pytest.mark.asyncio
async def test_resolve_stock_codes_filters_st_and_loss_pe_without_future_fundamentals() -> None:
    session = CaptureSession([FakeResult([{"ts_code": "000001.SZ"}, {"ts_code": "600000.SH"}])])

    result = await _resolve_stock_codes(
        session,
        user_id=1,
        target={"type": "all", "value": None},
        start_date=date(2026, 1, 1),
        filters={"exclude_st": True, "exclude_loss_pe": True},
    )

    sql = session.statements[0]
    assert result == ["000001.SZ", "600000.SH"]
    assert "s.is_delisted = FALSE" in sql
    assert "s.is_st = FALSE" in sql
    assert "LEFT JOIN LATERAL" in sql
    assert "sf.report_date <= :start_date" in sql
    assert "(f.pe_ttm IS NULL OR f.pe_ttm > 0)" in sql
    assert session.params[0]["start_date"] == date(2026, 1, 1)


@pytest.mark.asyncio
async def test_resolve_stock_codes_filters_multiple_markets() -> None:
    session = CaptureSession([FakeResult([{"ts_code": "000001.SZ"}])])

    result = await _resolve_stock_codes(
        session,
        user_id=1,
        target={"type": "market", "value": ["主板", "创业板"]},
        start_date=date(2026, 1, 1),
        filters={"exclude_st": True, "exclude_loss_pe": False},
    )

    sql = session.statements[0]
    assert result == ["000001.SZ"]
    assert "s.market IN (:market_0, :market_1)" in sql
    assert "s.is_st = FALSE" in sql
    assert session.params[0]["market_0"] == "主板"
    assert session.params[0]["market_1"] == "创业板"


@pytest.mark.asyncio
async def test_resolve_stock_codes_keeps_watchlist_filters_disabled_by_default() -> None:
    session = CaptureSession([FakeResult([{"ts_code": "000001.SZ"}])])

    result = await _resolve_stock_codes(
        session,
        user_id=7,
        target={"type": "watchlist_group", "value": "手动组合"},
        start_date=date(2026, 1, 1),
        filters=_filters_from_snapshot({"target": {"type": "watchlist_group", "value": "手动组合"}}),
    )

    sql = session.statements[0]
    assert result == ["000001.SZ"]
    assert "s.is_delisted = FALSE" in sql
    assert "s.is_st = FALSE" not in sql
    assert "LEFT JOIN LATERAL" not in sql
    assert "f.pe_ttm" not in sql
    assert session.params[0]["user_id"] == 7
    assert session.params[0]["group_name"] == "手动组合"
