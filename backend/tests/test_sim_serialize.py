"""Unit tests for the extracted simulation serialization helpers.

These are pure functions (no DB / Redis), so they run in the sandbox without
infrastructure. They pin the Decimal/date -> JSON-string contract that the API
layer relies on for every simulation response.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.sim.serialize import (
    MONEY_QUANT,
    _dec,
    _dict_value,
    _json,
    _money,
    _serialize_row,
    serialize_rows,
)


def test_serialize_row_decimal_to_str():
    row = {"price": Decimal("12.3000"), "name": "平安银行"}
    out = _serialize_row(row)
    assert out["price"] == "12.3000"
    assert out["name"] == "平安银行"


def test_serialize_row_date_datetime_to_iso():
    row = {"d": date(2026, 1, 2), "ts": datetime(2026, 1, 2, 9, 30, 5)}
    out = _serialize_row(row)
    assert out["d"] == "2026-01-02"
    assert out["ts"] == "2026-01-02T09:30:05"


def test_serialize_rows_list():
    rows = [{"x": Decimal("1")}, {"x": Decimal("2")}]
    out = serialize_rows(rows)
    assert [r["x"] for r in out] == ["1", "2"]


def test_dec_handles_none_and_types():
    assert _dec(None) == Decimal("0")
    assert _dec("3.5") == Decimal("3.5")
    assert _dec(Decimal("9")) == Decimal("9")
    assert _dec(None, default="1") == Decimal("1")


def test_money_quantizes_and_zero_normalizes():
    assert _money(Decimal("1.23456")) == Decimal("1.2346")
    assert _money(Decimal("0")) == Decimal("0.0000")
    assert MONEY_QUANT == Decimal("0.0001")


def test_json_serializes_none_as_empty_object():
    assert _json(None) == "{}"
    assert _json({"a": 1}) == '{"a": 1}'


def test_dict_value_parses_json_string_and_passthrough():
    assert _dict_value(None) == {}
    assert _dict_value("not json") == {}
    assert _dict_value({"k": 1}) == {"k": 1}
    assert _dict_value('{"k": 2}') == {"k": 2}
