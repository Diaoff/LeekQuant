"""Serialization helpers for simulation rows.

Decimal / date / datetime -> JSON-safe primitives. Extracted from the formerly
1600+ line ``sim/service.py`` so the widely-imported ``serialize_rows`` helper
lives in its own small, testable module with no cross-module dependencies.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.convert import _dec
import logging
logger = logging.getLogger(__name__)

MONEY_QUANT = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:
    quantized = value.quantize(MONEY_QUANT)
    return Decimal("0").quantize(MONEY_QUANT) if quantized == 0 else quantized


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            logger.debug("silent except in _dict_value")
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            payload[key] = str(value)
        elif isinstance(value, (date, datetime)):
            payload[key] = value.isoformat()
        else:
            payload[key] = value
    return payload


def serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_serialize_row(dict(row)) for row in rows]
