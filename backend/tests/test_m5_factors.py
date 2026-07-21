from datetime import date, timedelta
from decimal import Decimal
import json

import pytest

from app.factor.service import (
    _upsert_scoring_rank,
    analyze_factor_icir,
    compute_factors_for_date,
    normalize_cross_section,
    seed_factor_definitions,
)


class FakeResult:
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

    def scalar_one(self):
        return self._scalar if self._scalar is not None else self._rows[0]

    def scalar_one_or_none(self):
        return self._scalar


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


def enabled_definitions():
    return [
        {"name": "pe_ttm", "direction": -1, "default_weight": Decimal("1.0"), "expression": "pe_ttm"},
        {"name": "pb", "direction": -1, "default_weight": Decimal("1.0"), "expression": "pb"},
        {"name": "roe", "direction": 1, "default_weight": Decimal("1.2"), "expression": "roe"},
        {"name": "revenue_growth", "direction": 1, "default_weight": Decimal("1.0"), "expression": "revenue_growth"},
        {"name": "mom_20d", "direction": 1, "default_weight": Decimal("1.0"), "expression": "$close / Ref($close, 20) - 1"},
        {"name": "mom_60d", "direction": 1, "default_weight": Decimal("1.0"), "expression": "$close / Ref($close, 60) - 1"},
        {"name": "rsi6", "direction": 1, "default_weight": Decimal("0.8"), "expression": "RSI($close, 6)"},
        {"name": "vol_20d", "direction": -1, "default_weight": Decimal("0.8"), "expression": "STD($close / Ref($close, 1) - 1, 20)"},
    ]


def enabled_definitions_with_custom():
    return [
        *enabled_definitions(),
        {"name": "user_custom_alpha", "direction": 1, "default_weight": Decimal("1.0"), "expression": "$close / Ref($close, 5) - 1"},
    ]


def kline_rows():
    rows = []
    start = date(2026, 1, 1)
    for code, base, step in [
        ("000001.SZ", Decimal("10"), Decimal("0.04")),
        ("000002.SZ", Decimal("12"), Decimal("0.02")),
        ("600000.SH", Decimal("8"), Decimal("0.01")),
    ]:
        for i in range(61):
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": start + timedelta(days=i),
                    "close": base + step * i,
                    "volume": 100000 + i,
                }
            )
    return rows


@pytest.mark.asyncio
async def test_seed_factor_definitions_uses_idempotent_upsert():
    session = CaptureSession()

    count = await seed_factor_definitions(session)

    assert count == 8
    assert "ON CONFLICT (name) DO NOTHING" in session.statements[0]
    assert session.params[0][0]["name"] == "pe_ttm"
    assert session.params[0][0]["direction"] == -1
    assert session.commits == 1


@pytest.mark.asyncio
async def test_seed_factor_definitions_does_not_overwrite_existing_user_config():
    session = CaptureSession()

    await seed_factor_definitions(session)

    sql = session.statements[0]
    assert "DO UPDATE SET" not in sql
    for protected_column in ("enabled", "default_weight", "expression", "category", "display_name", "description"):
        assert f"{protected_column} = EXCLUDED.{protected_column}" not in sql


def test_normalize_cross_section_winsorizes_direction_and_percentile():
    result = normalize_cross_section(
        {
            "A": Decimal("1"),
            "B": Decimal("2"),
            "C": Decimal("1000"),
            "D": None,
        },
        direction=-1,
    )

    assert set(result) == {"A", "B", "C"}
    assert result["A"]["normalized_value"] > result["B"]["normalized_value"]
    assert result["A"]["percentile_rank"] == Decimal("1.00000000")
    assert "D" not in result


@pytest.mark.asyncio
async def test_compute_factors_for_date_writes_values_and_weighted_rank():
    session = CaptureSession(
        [
            FakeResult(scalar=True),
            FakeResult([]),
            FakeResult(enabled_definitions()),
            FakeResult([]),
            FakeResult([]),
            FakeResult(
                [
                    {"ts_code": "000001.SZ", "pe_ttm": Decimal("8"), "pb": Decimal("0.8"), "roe": Decimal("0.16"), "revenue_growth": Decimal("0.12")},
                    {"ts_code": "000002.SZ", "pe_ttm": Decimal("12"), "pb": Decimal("1.1"), "roe": Decimal("0.10"), "revenue_growth": Decimal("0.08")},
                    {"ts_code": "600000.SH", "pe_ttm": Decimal("20"), "pb": Decimal("1.8"), "roe": Decimal("0.06"), "revenue_growth": Decimal("0.02")},
                ]
            ),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    result = await compute_factors_for_date(session, trade_date=date(2026, 3, 2))

    assert result["trade_date"] == "2026-03-02"
    assert result["factor_count"] == 8
    assert result["rank_count"] == 3
    assert "FROM trade_calendar" in session.statements[0]
    assert "DELETE FROM factor_values" in session.statements[3]
    assert "DELETE FROM scoring_rank" in session.statements[4]
    assert "INSERT INTO factor_values" in session.statements[7]
    assert "ON CONFLICT (ts_code, trade_date, factor_name)" in session.statements[7]
    assert "INSERT INTO scoring_rank" in session.statements[8]
    assert session.commits == 1
    rank_rows = session.params[8]
    assert rank_rows[0]["rank"] == 1
    assert rank_rows[0]["ts_code"] == "000001.SZ"
    assert json.loads(rank_rows[0]["factor_breakdown"])["roe"]["weight"] == "1.2"


@pytest.mark.asyncio
async def test_compute_factors_for_explicit_non_trading_day_skips_without_writes():
    session = CaptureSession([FakeResult(scalar=False)])

    result = await compute_factors_for_date(session, trade_date=date(2026, 3, 7))

    assert result == {"skipped": True, "reason": "non-trading day"}
    assert "FROM trade_calendar" in session.statements[0]
    assert len(session.statements) == 1
    assert session.commits == 0


@pytest.mark.asyncio
async def test_compute_factors_only_clears_computable_builtin_factor_values():
    session = CaptureSession(
        [
            FakeResult(scalar=True),
            FakeResult([]),
            FakeResult(enabled_definitions_with_custom()),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
            FakeResult([]),
        ]
    )

    result = await compute_factors_for_date(session, trade_date=date(2026, 3, 2))

    assert result["factor_count"] == 9
    assert "DELETE FROM factor_values" in session.statements[3]
    all_names = {row["name"] for row in enabled_definitions()} | {"user_custom_alpha"}
    assert set(session.params[3]["factor_names"]) == all_names


@pytest.mark.asyncio
async def test_compute_watchlist_scope_only_ranks_group_members():
    session = CaptureSession(
        [
            FakeResult(scalar=True),
            FakeResult([]),
            FakeResult(enabled_definitions()),
            FakeResult([]),
            FakeResult([]),
            FakeResult(
                [
                    {"ts_code": "000001.SZ", "pe_ttm": Decimal("8"), "pb": Decimal("0.8"), "roe": Decimal("0.16"), "revenue_growth": Decimal("0.12")},
                    {"ts_code": "000002.SZ", "pe_ttm": Decimal("12"), "pb": Decimal("1.1"), "roe": Decimal("0.10"), "revenue_growth": Decimal("0.08")},
                    {"ts_code": "600000.SH", "pe_ttm": Decimal("20"), "pb": Decimal("1.8"), "roe": Decimal("0.06"), "revenue_growth": Decimal("0.02")},
                ]
            ),
            FakeResult(kline_rows()),
            FakeResult([]),
            FakeResult([{"ts_code": "000002.SZ"}]),
            FakeResult([]),
        ]
    )

    result = await compute_factors_for_date(
        session,
        trade_date=date(2026, 3, 2),
        scope_type="watchlist_group",
        scope_value="价值",
    )

    assert result["rank_count"] == 1
    assert session.commits == 1
    assert "DELETE FROM scoring_rank" in session.statements[4]
    assert session.params[4]["scope_type"] == "watchlist_group"
    assert session.params[4]["scope_value"] == "价值"
    assert "FROM watchlist" in session.statements[8]
    assert session.params[9][0]["ts_code"] == "000002.SZ"
    assert session.params[9][0]["scope_type"] == "watchlist_group"
    assert session.params[9][0]["scope_value"] == "价值"


@pytest.mark.asyncio
async def test_scoring_rank_uses_factor_definition_default_weights():
    factor_rows = [
        {
            "ts_code": "000001.SZ",
            "factor_name": "cheap",
            "value": Decimal("1"),
            "normalized_value": Decimal("1"),
            "percentile_rank": Decimal("1"),
        },
        {
            "ts_code": "000002.SZ",
            "factor_name": "cheap",
            "value": Decimal("1"),
            "normalized_value": Decimal("-1"),
            "percentile_rank": Decimal("0"),
        },
        {
            "ts_code": "000001.SZ",
            "factor_name": "quality",
            "value": Decimal("1"),
            "normalized_value": Decimal("-1"),
            "percentile_rank": Decimal("0"),
        },
        {
            "ts_code": "000002.SZ",
            "factor_name": "quality",
            "value": Decimal("1"),
            "normalized_value": Decimal("1"),
            "percentile_rank": Decimal("1"),
        },
    ]

    cheap_weighted = CaptureSession([FakeResult([])])
    await _upsert_scoring_rank(
        cheap_weighted,
        trade_date=date(2026, 3, 2),
        scope_type="all",
        scope_value=None,
        factor_rows=factor_rows,
        definitions=[
            {"name": "cheap", "default_weight": Decimal("5")},
            {"name": "quality", "default_weight": Decimal("1")},
        ],
    )

    quality_weighted = CaptureSession([FakeResult([])])
    await _upsert_scoring_rank(
        quality_weighted,
        trade_date=date(2026, 3, 2),
        scope_type="all",
        scope_value=None,
        factor_rows=factor_rows,
        definitions=[
            {"name": "cheap", "default_weight": Decimal("1")},
            {"name": "quality", "default_weight": Decimal("5")},
        ],
    )

    assert cheap_weighted.params[0][0]["rank"] == 1
    assert cheap_weighted.params[0][0]["ts_code"] == "000001.SZ"
    assert cheap_weighted.params[0][0]["total_score"] == Decimal("0.66666667")
    assert quality_weighted.params[0][0]["rank"] == 1
    assert quality_weighted.params[0][0]["ts_code"] == "000002.SZ"
    assert quality_weighted.params[0][0]["total_score"] == Decimal("0.66666667")


@pytest.mark.asyncio
async def test_compute_watchlist_scope_requires_scope_value_before_writes():
    session = CaptureSession()

    with pytest.raises(ValueError, match="scope_value is required"):
        await compute_factors_for_date(
            session,
            trade_date=date(2026, 3, 2),
            scope_type="watchlist_group",
            scope_value=None,
        )

    assert session.statements == []


@pytest.mark.asyncio
async def test_analyze_factor_icir_writes_summary_metrics():
    rows = [
        {"ts_code": "A", "trade_date": date(2026, 5, 1), "factor_value": Decimal("1"), "forward_return": Decimal("0.01")},
        {"ts_code": "B", "trade_date": date(2026, 5, 1), "factor_value": Decimal("2"), "forward_return": Decimal("0.02")},
        {"ts_code": "C", "trade_date": date(2026, 5, 1), "factor_value": Decimal("3"), "forward_return": Decimal("0.03")},
        {"ts_code": "A", "trade_date": date(2026, 5, 2), "factor_value": Decimal("1"), "forward_return": Decimal("0.03")},
        {"ts_code": "B", "trade_date": date(2026, 5, 2), "factor_value": Decimal("2"), "forward_return": Decimal("0.02")},
        {"ts_code": "C", "trade_date": date(2026, 5, 2), "factor_value": Decimal("3"), "forward_return": Decimal("0.01")},
    ]
    session = CaptureSession([FakeResult(rows), FakeResult([])])

    result = await analyze_factor_icir(
        session,
        factor_name="roe",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 2),
        forward_days=5,
    )

    assert result["ic_count"] == 2
    assert result["ic_mean"] == "0E-8"
    assert result["ic_std"] == "1.00000000"
    assert result["ir"] == "0E-8"
    assert result["ic_gt_0_pct"] == "0.50000000"
    assert "ON CONFLICT (factor_name, period_start, period_end, forward_days)" in session.statements[1]
