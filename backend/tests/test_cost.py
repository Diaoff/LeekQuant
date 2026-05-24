"""Tests for A-share trading cost calculator (cost.py).

Covers:
- Buy fee calculation (commission + transfer fee, no stamp tax)
- Sell fee calculation (commission + stamp tax + transfer fee)
- Minimum commission threshold (5 yuan)
- Custom FeeConfig override
- Edge cases: zero amount, large amounts, precision
"""
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.backtest.cost import AShareCostCalculator, CostResult, FeeConfig


@pytest.mark.cost
class TestBuyFeeCalculation:
    """Test buy-side fee calculations."""

    def test_normal_buy_commission_above_minimum(self):
        """正常金额买入佣金 > 最低佣金(5元)"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("100000"))
        assert result.commission == Decimal("25.0000")
        assert result.stamp_tax == Decimal("0.0000")

    def test_small_buy_triggers_minimum_commission(self):
        """小额交易触发最低佣金 5 元

        10000 * 0.025% = 2.5 < 5, 所以取 5
        """
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("10000"))
        assert result.commission == Decimal("5.0000")

    def test_large_buy_commission(self):
        """大额交易佣金计算: 1000000 * 0.025% = 250"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("1000000"))
        assert result.commission == Decimal("250.0000")

    def test_buy_no_stamp_tax(self):
        """买入无印花税"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("50000"))
        assert result.stamp_tax == Decimal("0.0000")

    def test_buy_transfer_fee(self):
        """买入过户费计算: 50000 * 0.001% = 0.5"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("50000"))
        assert result.transfer_fee == Decimal("0.5000")

    def test_buy_total_fee_equals_commission_plus_transfer(self):
        """买入总费用 = 佣金 + 过户费（无印花税）"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("100000"))
        expected = result.commission + result.transfer_fee
        assert result.total_fee == expected

    def test_buy_at_minimum_threshold(self):
        """刚好达到最低佣金阈值: 200000 * 0.025% = 50 > 5, 所以不触发最低佣金"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("200000"))
        assert result.commission == Decimal("50.0000")


@pytest.mark.cost
class TestSellFeeCalculation:
    """Test sell-side fee calculations."""

    def test_normal_sell_commission(self):
        """正常金额卖出佣金计算"""
        calc = AShareCostCalculator()
        result = calc.calculate("卖出", Decimal("100000"))
        assert result.commission == Decimal("25.0000")

    def test_sell_includes_stamp_tax(self):
        """卖出包含印花税: 100000 * 0.05% = 50"""
        calc = AShareCostCalculator()
        result = calc.calculate("卖出", Decimal("100000"))
        assert result.stamp_tax == Decimal("50.0000")

    def test_sell_transfer_fee(self):
        """卖出过户费"""
        calc = AShareCostCalculator()
        result = calc.calculate("卖出", Decimal("100000"))
        assert result.transfer_fee == Decimal("1.0000")

    def test_sell_total_fee_is_sum_of_all_three(self):
        """卖出总费用 = 佣金 + 印花税 + 过户费"""
        calc = AShareCostCalculator()
        result = calc.calculate("卖出", Decimal("100000"))
        expected = result.commission + result.stamp_tax + result.transfer_fee
        assert result.total_fee == expected

    def test_sell_small_amount_minimum_commission(self):
        """小额卖出也触发最低佣金"""
        calc = AShareCostCalculator()
        result = calc.calculate("卖出", Decimal("10000"))
        assert result.commission == Decimal("5.0000")
        assert result.stamp_tax == Decimal("5.0000")


@pytest.mark.cost
class TestFeePrecision:
    """Test rounding precision (ROUND_HALF_UP to 4 decimal places)."""

    def test_rounding_half_up_four_decimals(self):
        """四舍五入到4位小数精度验证"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("33333"))
        commission = Decimal("33333") * Decimal("0.00025")
        assert result.commission == commission.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def test_result_fields_are_quantized(self):
        """所有结果字段都应量化到4位小数"""
        calc = AShareCostCalculator()
        result = calc.calculate("卖出", Decimal("123456"))
        for field_name, value in [
            ("commission", result.commission),
            ("stamp_tax", result.stamp_tax),
            ("transfer_fee", result.transfer_fee),
            ("total_fee", result.total_fee),
        ]:
            assert value.as_tuple().exponent >= -4, f"{field_name} has more than 4 decimals"


@pytest.mark.cost
class TestCustomFeeConfig:
    """Test custom FeeConfig overrides."""

    def test_custom_commission_rate(self):
        """通过 FeeConfig 自定义佣金率"""
        config = FeeConfig(commission_rate=Decimal("0.0003"))
        calc = AShareCostCalculator(config)
        result = calc.calculate("买入", Decimal("100000"))
        assert result.commission == Decimal("30.0000")

    def test_custom_min_commission(self):
        """通过 FeeConfig 自定义最低佣金"""
        config = FeeConfig(min_commission=Decimal("10"))
        calc = AShareCostCalculator(config)
        result = calc.calculate("买入", Decimal("10000"))
        assert result.commission == Decimal("10.0000")

    def test_custom_stamp_tax_rate(self):
        """自定义印花税率"""
        config = FeeConfig(stamp_tax_rate=Decimal("0.001"))
        calc = AShareCostCalculator(config)
        result = calc.calculate("卖出", Decimal("100000"))
        assert result.stamp_tax == Decimal("100.0000")

    def test_default_values_match_documentation(self):
        """验证默认值与文档一致:
        - 佣金率: 0.025%
        - 最低佣金: 5元
        - 印花税: 0.05%（仅卖出）
        - 过户费: 0.001%
        """
        config = FeeConfig()
        assert config.commission_rate == Decimal("0.00025")
        assert config.min_commission == Decimal("5.0")
        assert config.stamp_tax_rate == Decimal("0.0005")
        assert config.transfer_fee_rate == Decimal("0.00001")
        assert config.waive_min_commission is False

    def test_calculator_uses_provided_config(self):
        """计算器使用传入的配置，而非默认配置"""
        config = FeeConfig(
            commission_rate=Decimal("0.001"),
            min_commission=Decimal("1"),
            stamp_tax_rate=Decimal("0"),
            transfer_fee_rate=Decimal("0"),
        )
        calc = AShareCostCalculator(config)
        result = calc.calculate("买入", Decimal("1000"))
        assert result.commission == Decimal("1.0000")
        assert result.total_fee == Decimal("1.0000")

    def test_waive_min_commission_uses_proportional_commission(self):
        """免5时小额交易不触发最低佣金"""
        config = FeeConfig(waive_min_commission=True)
        calc = AShareCostCalculator(config)
        result = calc.calculate("买入", Decimal("10000"))
        assert result.commission == Decimal("2.5000")

    def test_string_false_does_not_enable_waive_min_commission(self):
        from app.backtest.cost import build_fee_config

        config = build_fee_config({"waive_min_commission": "false"})
        calc = AShareCostCalculator(config)
        result = calc.calculate("买入", Decimal("10000"))
        assert result.commission == Decimal("5.0000")


@pytest.mark.cost
class TestEdgeCases:
    """Test boundary and edge cases."""

    def test_zero_amount_returns_zero_fees(self):
        """amount = 0 时费用应为 0 或最低佣金"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("0"))
        assert result.commission >= Decimal("0")
        assert result.total_fee >= Decimal("0")

    def test_very_large_amount(self):
        """极大金额（百万级）计算正确"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("10000000"))
        assert result.commission == Decimal("2500.0000")
        assert result.transfer_fee == Decimal("100.0000")

    def test_very_large_sell_amount(self):
        """极大金额卖出包含印花税"""
        calc = AShareCostCalculator()
        result = calc.calculate("卖出", Decimal("5000000"))
        assert result.commission == Decimal("1250.0000")
        assert result.stamp_tax == Decimal("2500.0000")

    def test_one_yuan_amount(self):
        """1元交易触发最低佣金"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("1"))
        assert result.commission == Decimal("5.0000")

    def test_cost_result_is_dataclass(self):
        """验证 CostResult 是 dataclass 且字段完整"""
        calc = AShareCostCalculator()
        result = calc.calculate("买入", Decimal("10000"))
        assert isinstance(result, CostResult)
        assert hasattr(result, "commission")
        assert hasattr(result, "stamp_tax")
        assert hasattr(result, "transfer_fee")
        assert hasattr(result, "total_fee")

    def test_buy_vs_sell_difference(self):
        """买入和卖出的差异：卖出有印花税"""
        calc = AShareCostCalculator()
        buy_result = calc.calculate("买入", Decimal("100000"))
        sell_result = calc.calculate("卖出", Decimal("100000"))
        assert sell_result.total_fee > buy_result.total_fee
        assert sell_result.stamp_tax > buy_result.stamp_tax


@pytest.mark.parametrize("amount,expected_commission", [
    (Decimal("200000"), Decimal("50.0000")),
    (Decimal("400000"), Decimal("100.0000")),
    (Decimal("1000000"), Decimal("250.0000")),
])
def test_parametrized_commission_calculation(amount, expected_commission):
    """参数化测试：不同金额的佣金计算"""
    calc = AShareCostCalculator()
    result = calc.calculate("买入", amount)
    assert result.commission == expected_commission


@pytest.mark.parametrize("amount,expected_min_trigger", [
    (Decimal("19999"), True),
    (Decimal("20001"), False),
    (Decimal("50000"), False),
])
def test_parametrized_minimum_commission_threshold(amount, expected_min_trigger):
    """参数化测试：最低佣金触发阈值边界

    阈值 = 5 / 0.00025 = 20000
    """
    calc = AShareCostCalculator()
    result = calc.calculate("买入", amount)
    if expected_min_trigger:
        assert result.commission == Decimal("5.0000")
    else:
        assert result.commission > Decimal("5.0000")
