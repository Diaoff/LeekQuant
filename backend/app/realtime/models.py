from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

TS_CODE_PATTERN = r"^\d{6}\.(SH|SZ|BJ)$"
TICK_FIELDS = ("ts_code", "price", "change", "change_pct", "volume", "amount", "bid1", "ask1", "ts")


def normalize_ts_code(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("ts_code is required")
    return normalized


def realtime_channel(ts_code: str) -> str:
    return f"realtime:{normalize_ts_code(ts_code)}"


def _decimal_to_json(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


class RealtimeTick(BaseModel):
    ts_code: str = Field(pattern=TS_CODE_PATTERN)
    price: Decimal
    change: Decimal = Decimal("0")
    change_pct: Decimal = Decimal("0")
    volume: int = 0
    amount: Decimal = Decimal("0")
    bid1: Decimal | None = None
    ask1: Decimal | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("ts_code", mode="before")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return normalize_ts_code(value)

    @field_validator("price", "change", "change_pct", "amount", "bid1", "ask1", mode="before")
    @classmethod
    def parse_decimal(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    def to_payload(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "price": _decimal_to_json(self.price),
            "change": _decimal_to_json(self.change),
            "change_pct": _decimal_to_json(self.change_pct),
            "volume": self.volume,
            "amount": _decimal_to_json(self.amount),
            "bid1": _decimal_to_json(self.bid1),
            "ask1": _decimal_to_json(self.ask1),
            "ts": self.ts.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RealtimeTick":
        return cls(**{field: payload.get(field) for field in TICK_FIELDS if field in payload})
