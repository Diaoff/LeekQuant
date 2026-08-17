"""Shared numeric coercion helpers.

These were previously duplicated across ``sim/service.py``,
``tasks/signal_tasks.py`` (``_dec``) and ``data/service.py`` / ``backtest/cost.py``
(``_as_decimal`` with two incompatible signatures). Centralizing them here removes
the duplication and guarantees identical coercion semantics everywhere.

Pure stdlib only — no project imports — so this module is safe to import from
anywhere without creating circular-dependency risk.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

_DECIMAL_ZERO = Decimal("0")


def _dec(value: Any, default: str = "0") -> Decimal:
    """Coerce *value* to :class:`~decimal.Decimal`.

    ``None`` falls back to ``Decimal(default)`` (default ``"0"``); an already-Decimal
    value is returned unchanged; everything else is parsed via ``Decimal(str(value))``.
    """
    if value is None:
        return Decimal(default)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _as_decimal(value: Any, default: Any = None) -> Decimal | None:
    """Coerce *value* to :class:`~decimal.Decimal`, tolerating ``None``.

    - ``value is None`` -> returns *default* (which is ``None`` unless the caller
      supplies a fallback, e.g. ``_as_decimal(x, default=Decimal("0"))``).
    - an already-Decimal value is returned unchanged.
    - anything else is parsed via ``Decimal(str(value))``.

    This single signature supersedes the two former variants:
    ``data/service._as_decimal`` (no default -> ``None``) and
    ``backtest/cost._as_decimal`` (required default -> that default).
    """
    if value is None:
        return default
    return value if isinstance(value, Decimal) else Decimal(str(value))


__all__ = ["_dec", "_as_decimal", "_DECIMAL_ZERO"]
