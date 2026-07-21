"""Tests for the factor expression evaluator."""
from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from app.factor.expression import (
    FactorContext,
    ParseError,
    evaluate_expression,
    parse,
    tokenize,
    validate_expression,
)


def _make_ctx(closes: list[float], length: int | None = None) -> FactorContext:
    n = length or len(closes)
    return FactorContext(
        kline={
            "$close": np.array(closes, dtype=np.float64),
            "$open": np.array(closes, dtype=np.float64),
            "$high": np.array(closes, dtype=np.float64),
            "$low": np.array(closes, dtype=np.float64),
            "$volume": np.full(n, 1000.0, dtype=np.float64),
            "$amount": np.full(n, 10000.0, dtype=np.float64),
        },
        fundamentals={},
        length=n,
    )


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def test_tokenize_number() -> None:
    tokens = tokenize("42")
    assert len(tokens) == 2  # NUMBER + EOF
    assert tokens[0].value == "42"


def test_tokenize_ident() -> None:
    tokens = tokenize("RSI")
    assert tokens[0].value == "RSI"


def test_tokenize_operators() -> None:
    tokens = tokenize("1 + 2 * 3")
    values = [t.value for t in tokens[:-1]]
    assert values == ["1", "+", "2", "*", "3"]


def test_tokenize_dollar_var() -> None:
    tokens = tokenize("$close")
    assert tokens[0].value == "$close"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parse_number_literal() -> None:
    ast = parse("42")
    ctx = _make_ctx([1.0, 2.0, 3.0])
    result = ast.evaluate(ctx)
    assert np.allclose(result, [42.0, 42.0, 42.0])


def test_parse_variable() -> None:
    ast = parse("$close")
    ctx = _make_ctx([10.0, 11.0, 12.0])
    result = ast.evaluate(ctx)
    assert np.allclose(result, [10.0, 11.0, 12.0])


def test_parse_binary_add() -> None:
    ast = parse("$close + 1")
    ctx = _make_ctx([10.0, 20.0, 30.0])
    result = ast.evaluate(ctx)
    assert np.allclose(result, [11.0, 21.0, 31.0])


def test_parse_binary_precedence() -> None:
    ast = parse("1 + 2 * 3")
    ctx = _make_ctx([0.0])
    result = ast.evaluate(ctx)
    assert np.allclose(result, [7.0])


def test_parse_unary_minus() -> None:
    ast = parse("-$close")
    ctx = _make_ctx([10.0, 20.0])
    result = ast.evaluate(ctx)
    assert np.allclose(result, [-10.0, -20.0])


def test_parse_parentheses() -> None:
    ast = parse("(1 + 2) * 3")
    ctx = _make_ctx([0.0])
    result = ast.evaluate(ctx)
    assert np.allclose(result, [9.0])


def test_parse_complex_expression() -> None:
    ast = parse("$close / Ref($close, 1) - 1")
    ctx = _make_ctx([10.0, 11.0, 12.0])
    result = ast.evaluate(ctx)
    expected = np.array([np.nan, 11.0 / 10.0 - 1, 12.0 / 11.0 - 1])
    assert np.isclose(result[1], expected[1])
    assert np.isclose(result[2], expected[2])


def test_parse_nested_function() -> None:
    ast = parse("MA($close, 3)")
    ctx = _make_ctx([10.0, 11.0, 12.0, 13.0, 14.0])
    result = ast.evaluate(ctx)
    assert result[-1] == pytest.approx(13.0)


def test_parse_syntax_error() -> None:
    with pytest.raises(ParseError):
        parse("$close +")


def test_parse_unexpected_token() -> None:
    with pytest.raises(ParseError):
        parse("1 2")


# ---------------------------------------------------------------------------
# Variable resolution
# ---------------------------------------------------------------------------

def test_variable_kline() -> None:
    ctx = _make_ctx([100.0, 200.0, 300.0])
    result = evaluate_expression("$close", ctx)
    assert np.allclose(result, [100.0, 200.0, 300.0])


def test_variable_fundamental() -> None:
    ctx = FactorContext(
        kline={"$close": np.array([1.0, 2.0])},
        fundamentals={"pe_ttm": 15.5},
        length=2,
    )
    result = evaluate_expression("pe_ttm", ctx)
    assert np.allclose(result, [15.5, 15.5])


def test_variable_fundamental_alias() -> None:
    ctx = FactorContext(
        kline={"$close": np.array([1.0])},
        fundamentals={"roe": 0.25},
        length=1,
    )
    result = evaluate_expression("ROE", ctx)
    assert np.allclose(result, [0.25])


def test_variable_missing_fundamental() -> None:
    ctx = FactorContext(
        kline={"$close": np.array([1.0, 2.0])},
        fundamentals={},
        length=2,
    )
    result = evaluate_expression("pe_ttm", ctx)
    assert np.all(np.isnan(result))


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def test_mytt_rsi() -> None:
    ctx = _make_ctx([10.0, 11.0, 12.0, 11.0, 10.0, 11.0, 12.0, 11.0, 10.0, 11.0])
    result = evaluate_expression("RSI($close, 6)", ctx)
    assert result[-1] > 0
    assert result[-1] < 100


def test_mytt_ma() -> None:
    ctx = _make_ctx([10.0, 11.0, 12.0, 13.0, 14.0])
    result = evaluate_expression("MA($close, 3)", ctx)
    assert result[-1] == pytest.approx(13.0)


def test_mytt_std() -> None:
    ctx = _make_ctx([10.0, 11.0, 12.0, 13.0, 14.0])
    result = evaluate_expression("STD($close, 3)", ctx)
    assert result[-1] > 0


def test_mytt_ref() -> None:
    ctx = _make_ctx([10.0, 20.0, 30.0])
    result = evaluate_expression("REF($close, 1)", ctx)
    assert np.isnan(result[0])
    assert result[1] == pytest.approx(10.0)
    assert result[2] == pytest.approx(20.0)


def test_math_abs() -> None:
    ctx = _make_ctx([-5.0, 3.0])
    result = evaluate_expression("ABS($close)", ctx)
    assert np.allclose(result, [5.0, 3.0])


def test_unknown_function() -> None:
    with pytest.raises(ValueError, match="unknown function"):
        evaluate_expression("FOOBAR($close, 6)", _make_ctx([1.0]))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_valid() -> None:
    is_valid, error = validate_expression("$close / Ref($close, 20) - 1")
    assert is_valid is True
    assert error is None


def test_validate_invalid_syntax() -> None:
    is_valid, error = validate_expression("$close +")
    assert is_valid is False
    assert error is not None


def test_validate_unknown_variable() -> None:
    is_valid, error = validate_expression("unknown_var")
    assert is_valid is False
    assert "unknown variable" in error


def test_validate_unknown_function() -> None:
    is_valid, error = validate_expression("FOOBAR($close)")
    assert is_valid is False
    assert "unknown function" in error


def test_validate_complex_expression() -> None:
    is_valid, error = validate_expression("MyTT.RSI($close, 6)")
    assert is_valid is True
    assert error is None


def test_validate_negation() -> None:
    is_valid, error = validate_expression("-pe_ttm")
    assert is_valid is True
    assert error is None


# ---------------------------------------------------------------------------
# Integration with service
# ---------------------------------------------------------------------------

def test_compute_raw_factor_values_with_expressions() -> None:
    from app.factor.service import _compute_raw_factor_values

    fundamentals = {
        "000001.SZ": {"pe_ttm": 15.0, "pb": 1.5, "roe": 0.2, "revenue_growth": 0.1},
    }
    kline_rows = [
        {"ts_code": "000001.SZ", "close": 10.0, "open": 9.5, "high": 10.5, "low": 9.0, "volume": 1000, "amount": 10000},
        {"ts_code": "000001.SZ", "close": 11.0, "open": 10.0, "high": 11.5, "low": 9.5, "volume": 1200, "amount": 12000},
        {"ts_code": "000001.SZ", "close": 12.0, "open": 11.0, "high": 12.5, "low": 10.5, "volume": 1400, "amount": 14000},
    ]

    definitions = [
        {"name": "pe_inv", "expression": "-pe_ttm"},
        {"name": "mom_2d", "expression": "$close / Ref($close, 2) - 1"},
    ]

    raw = _compute_raw_factor_values(fundamentals, kline_rows, definitions)

    assert "pe_inv" in raw
    assert "000001.SZ" in raw["pe_inv"]
    assert raw["pe_inv"]["000001.SZ"] == Decimal("-15.0")

    assert "mom_2d" in raw
    assert "000001.SZ" in raw["mom_2d"]
    expected_mom = 12.0 / 10.0 - 1
    assert float(raw["mom_2d"]["000001.SZ"]) == pytest.approx(expected_mom, abs=0.0001)


def test_builtin_factors_still_work_with_expressions() -> None:
    from app.factor.service import _compute_raw_factor_values

    fundamentals = {
        "000001.SZ": {"pe_ttm": 15.0, "pb": 1.5, "roe": 0.2, "revenue_growth": 0.1},
    }
    kline_rows = [
        {"ts_code": "000001.SZ", "close": 10.0 + i, "open": 9.5 + i, "high": 10.5 + i, "low": 9.0 + i, "volume": 1000 + i * 100, "amount": 10000 + i * 1000}
        for i in range(25)
    ]

    definitions = [
        {"name": "pe_ttm", "expression": "pe_ttm"},
        {"name": "pb", "expression": "pb"},
    ]

    raw = _compute_raw_factor_values(fundamentals, kline_rows, definitions)
    assert raw["pe_ttm"]["000001.SZ"] == Decimal("15.0")
    assert raw["pb"]["000001.SZ"] == Decimal("1.5")
