"""A-share trading cost calculator.

Handles commission, stamp tax, transfer fee calculation for backtesting.
Rates are configurable via backtest config.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(slots=True)
class CostResult:
    commission: Decimal
    stamp_tax: Decimal
    transfer_fee: Decimal
    total_fee: Decimal


@dataclass(slots=True)
class FeeConfig:
    commission_rate: Decimal = Decimal("0.00025")
    min_commission: Decimal = Decimal("5.0")
    stamp_tax_rate: Decimal = Decimal("0.0005")
    transfer_fee_rate: Decimal = Decimal("0.00001")


class AShareCostCalculator:
    """Calculate A-share trading fees for backtesting."""

    def __init__(self, config: FeeConfig | None = None):
        self.cfg = config or FeeConfig()

    def calculate(self, direction: str, amount: Decimal) -> CostResult:
        commission = max(amount * self.cfg.commission_rate, self.cfg.min_commission)
        stamp_tax = amount * self.cfg.stamp_tax_rate if direction == "卖出" else Decimal("0")
        transfer_fee = amount * self.cfg.transfer_fee_rate
        total_fee = commission + stamp_tax + transfer_fee
        return CostResult(
            commission=commission.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            stamp_tax=stamp_tax.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            transfer_fee=transfer_fee.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            total_fee=total_fee.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        )
