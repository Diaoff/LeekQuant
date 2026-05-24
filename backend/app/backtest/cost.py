"""A-share trading cost calculator.

Handles commission, stamp tax, transfer fee calculation for backtesting.
Rates are configurable via backtest config.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

DEFAULT_COMMISSION_RATE = Decimal("0.00025")
DEFAULT_MIN_COMMISSION = Decimal("5.0")
DEFAULT_STAMP_TAX_RATE = Decimal("0.0005")
DEFAULT_TRANSFER_FEE_RATE = Decimal("0.00001")


@dataclass(slots=True)
class CostResult:
    commission: Decimal
    stamp_tax: Decimal
    transfer_fee: Decimal
    total_fee: Decimal


@dataclass(slots=True)
class FeeConfig:
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE
    min_commission: Decimal = DEFAULT_MIN_COMMISSION
    stamp_tax_rate: Decimal = DEFAULT_STAMP_TAX_RATE
    transfer_fee_rate: Decimal = DEFAULT_TRANSFER_FEE_RATE
    waive_min_commission: bool = False


FEE_CONFIG_FIELDS = tuple(field.name for field in fields(FeeConfig))


def _as_decimal(value: Any, default: Decimal) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def fee_config_to_dict(config: FeeConfig | None = None) -> dict[str, str | bool]:
    cfg = config or FeeConfig()
    return {
        "commission_rate": str(cfg.commission_rate),
        "min_commission": str(cfg.min_commission),
        "stamp_tax_rate": str(cfg.stamp_tax_rate),
        "transfer_fee_rate": str(cfg.transfer_fee_rate),
        "waive_min_commission": cfg.waive_min_commission,
    }


def build_fee_config(*overrides: dict[str, Any] | None) -> FeeConfig:
    """Build a fee config from defaults plus partial override dictionaries."""
    values: dict[str, Any] = fee_config_to_dict()
    for override in overrides:
        if not isinstance(override, dict):
            continue
        for key in FEE_CONFIG_FIELDS:
            if key in override and override[key] is not None:
                values[key] = override[key]
    return FeeConfig(
        commission_rate=_as_decimal(values.get("commission_rate"), DEFAULT_COMMISSION_RATE),
        min_commission=_as_decimal(values.get("min_commission"), DEFAULT_MIN_COMMISSION),
        stamp_tax_rate=_as_decimal(values.get("stamp_tax_rate"), DEFAULT_STAMP_TAX_RATE),
        transfer_fee_rate=_as_decimal(values.get("transfer_fee_rate"), DEFAULT_TRANSFER_FEE_RATE),
        waive_min_commission=_as_bool(values.get("waive_min_commission", False)),
    )


class AShareCostCalculator:
    """Calculate A-share trading fees for backtesting."""

    def __init__(self, config: FeeConfig | None = None):
        self.cfg = config or FeeConfig()

    def calculate(self, direction: str, amount: Decimal) -> CostResult:
        raw_commission = amount * self.cfg.commission_rate
        commission = raw_commission if self.cfg.waive_min_commission else max(raw_commission, self.cfg.min_commission)
        stamp_tax = amount * self.cfg.stamp_tax_rate if direction == "卖出" else Decimal("0")
        transfer_fee = amount * self.cfg.transfer_fee_rate
        total_fee = commission + stamp_tax + transfer_fee
        return CostResult(
            commission=commission.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            stamp_tax=stamp_tax.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            transfer_fee=transfer_fee.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            total_fee=total_fee.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        )
