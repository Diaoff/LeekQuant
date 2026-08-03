from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class FactorDefinition:
    name: str
    display_name: str
    category: str
    expression: str
    direction: int
    default_weight: Decimal
    enabled: bool
    description: str


BUILTIN_FACTORS: tuple[FactorDefinition, ...] = (
    FactorDefinition("pe_ttm", "PE TTM", "valuation", "pe_ttm", -1, Decimal("1.0"), True, "市盈率 TTM，越低越好"),
    FactorDefinition("pb", "PB", "valuation", "pb", -1, Decimal("1.0"), True, "市净率，越低越好"),
    FactorDefinition("roe", "ROE", "quality", "roe", 1, Decimal("1.2"), True, "净资产收益率，越高越好"),
    FactorDefinition("revenue_growth", "Revenue Growth", "growth", "revenue_growth", 1, Decimal("1.0"), True, "营业收入同比增速，越高越好"),
    FactorDefinition("mom_20d", "20D Momentum", "momentum", "$close / REF($close, 20) - 1", 1, Decimal("1.0"), True, "20 个交易日动量，越高越好"),
    FactorDefinition("mom_60d", "60D Momentum", "momentum", "$close / REF($close, 60) - 1", 1, Decimal("1.0"), True, "60 个交易日动量，越高越好"),
    FactorDefinition("rsi6", "RSI6", "momentum", "RSI($close, 6)", 1, Decimal("0.8"), True, "6 日 RSI，越高越强"),
    FactorDefinition("vol_20d", "20D Volatility", "volatility", "STD($close / REF($close, 1) - 1, 20)", -1, Decimal("0.8"), True, "20 个交易日收益波动率，越低越好"),
)
