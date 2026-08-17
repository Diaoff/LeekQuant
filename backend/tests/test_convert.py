"""Tests for the shared numeric coercion helpers in ``app.core.convert``.

These pin the behavior that previously lived in two divergent ``_as_decimal``
implementations (data/service.py vs backtest/cost.py) and a duplicated ``_dec``
(sim/serialize.py vs tasks/signal_tasks.py), so a future edit can't silently
change coercion semantics.
"""
import pytest
from decimal import Decimal

from app.core.convert import _as_decimal, _dec


class TestDec:
    def test_none_uses_default_zero(self):
        assert _dec(None) == Decimal("0")

    def test_none_with_explicit_default(self):
        assert _dec(None, "5") == Decimal("5")

    def test_string_is_parsed(self):
        assert _dec("3.14") == Decimal("3.14")

    def test_decimal_passes_through(self):
        assert _dec(Decimal("2")) == Decimal("2")

    def test_int_is_parsed(self):
        assert _dec(7) == Decimal("7")


class TestAsDecimal:
    def test_none_without_default_is_none(self):
        # data/service.py semantics
        assert _as_decimal(None) is None

    def test_none_with_default_returns_default(self):
        # backtest/cost.py semantics
        assert _as_decimal(None, default=Decimal("0")) == Decimal("0")
        assert _as_decimal(None, default=Decimal("1.5")) == Decimal("1.5")

    def test_string_is_parsed(self):
        assert _as_decimal("1.5") == Decimal("1.5")

    def test_decimal_passes_through(self):
        assert _as_decimal(Decimal("9.9")) == Decimal("9.9")

    def test_default_ignored_when_value_present(self):
        assert _as_decimal("9.9", default=Decimal("0")) == Decimal("9.9")
