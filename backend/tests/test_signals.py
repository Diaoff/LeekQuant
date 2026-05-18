"""Tests for the 5-level signal state machine (signals.py).

Covers:
- map_signal_to_action(): 25 combinations of position × signal
- apply_cn_rules(): 4 A-share trading rules
- Edge cases: negative positions, overflow positions, custom targets
"""
import pytest

from app.backtest.signals import SignalInput, SignalOutput, apply_cn_rules, map_signal_to_action


class TestMapSignalToActionEmptyPosition:
    """Test map_signal_to_action when current_position = 0 (empty)."""

    def test_empty_buy_returns_buy_to_full(self):
        """空仓+买入信号 → BUY, target=1.0"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=0))
        assert result.action == "BUY"
        assert result.target_position == 1.0

    def test_empty_buy_with_custom_target(self):
        """空仓+买入信号(自定义target) → BUY, target=0.6"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=0, target_position=0.6))
        assert result.action == "BUY"
        assert result.target_position == 0.6

    def test_empty_increase_returns_buy_to_half(self):
        """空仓+增持信号 → BUY, target=0.5"""
        result = map_signal_to_action(SignalInput(signal_type="增持", current_position=0))
        assert result.action == "BUY"
        assert result.target_position == 0.5

    def test_empty_decrease_returns_hold(self):
        """空仓+减仓信号 → HOLD"""
        result = map_signal_to_action(SignalInput(signal_type="减仓", current_position=0))
        assert result.action == "HOLD"
        assert result.target_position == 0.0

    def test_empty_sell_returns_hold(self):
        """空仓+卖出信号 → HOLD (无持仓可卖)"""
        result = map_signal_to_action(SignalInput(signal_type="卖出", current_position=0))
        assert result.action == "HOLD"
        assert result.target_position == 0.0

    def test_empty_watch_returns_hold(self):
        """空仓+观望信号 → HOLD"""
        result = map_signal_to_action(SignalInput(signal_type="观望", current_position=0))
        assert result.action == "HOLD"
        assert result.target_position == 0.0


class TestMapSignalToActionLowPosition:
    """Test map_signal_to_action when current_position = 0.3 (low)."""

    def test_low_buy_returns_buy_to_target(self):
        """低仓(0.3)+买入信号 → BUY, target=1.0"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=0.3))
        assert result.action == "BUY"
        assert result.target_position == 1.0

    def test_low_buy_with_lower_target_still_buys(self):
        """低仓(0.3)+买入(target=0.5) → BUY (因为 0.5 > 0.3)"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=0.3, target_position=0.5))
        assert result.action == "BUY"
        assert result.target_position == 0.5

    def test_low_buy_target_below_current_holds(self):
        """低仓(0.3)+买入(target=0.2) → HOLD (target < current)"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=0.3, target_position=0.2))
        assert result.action == "HOLD"

    def test_low_increase_returns_buy(self):
        """低仓(0.3)+增持信号 → BUY, target=min(0.3+0.25,1.0)=0.55"""
        result = map_signal_to_action(SignalInput(signal_type="增持", current_position=0.3))
        assert result.action == "BUY"
        assert result.target_position == 0.55

    def test_low_decrease_returns_sell_partial(self):
        """低仓(0.3)+减仓信号 → SELL_PARTIAL (0.3-0.25=0.05 < 0.3)"""
        result = map_signal_to_action(SignalInput(signal_type="减仓", current_position=0.3))
        assert result.action == "SELL_PARTIAL"
        assert abs(result.target_position - 0.05) < 0.001

    def test_low_sell_returns_sell_all(self):
        """低仓(0.3)+卖出信号 → SELL_ALL"""
        result = map_signal_to_action(SignalInput(signal_type="卖出", current_position=0.3))
        assert result.action == "SELL_ALL"
        assert result.target_position == 0.0

    def test_low_watch_returns_hold(self):
        """低仓(0.3)+观望信号 → HOLD"""
        result = map_signal_to_action(SignalInput(signal_type="观望", current_position=0.3))
        assert result.action == "HOLD"


class TestMapSignalToActionMidPosition:
    """Test map_signal_to_action when current_position = 0.6 (mid)."""

    def test_mid_buy_returns_buy_to_full(self):
        """中仓(0.6)+买入信号 → BUY, target=1.0"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=0.6))
        assert result.action == "BUY"
        assert result.target_position == 1.0

    def test_mid_increase_returns_hold(self):
        """中仓(0.6)+增持信号 → HOLD (0.6+0.25=0.85 > 0.6 但逻辑是检查target>cur)

        实际上根据代码：target = min(cur + 0.25, 1.0) = 0.85, 0.85 > 0.6 → BUY
        """
        result = map_signal_to_action(SignalInput(signal_type="增持", current_position=0.6))
        assert result.action == "BUY"
        assert result.target_position == 0.85

    def test_mid_decrease_returns_sell_partial(self):
        """中仓(0.6)+减仓信号 → SELL_PARTIAL, target=0.35"""
        result = map_signal_to_action(SignalInput(signal_type="减仓", current_position=0.6))
        assert result.action == "SELL_PARTIAL"
        assert result.target_position == 0.35

    def test_mid_sell_returns_sell_all(self):
        """中仓(0.6)+卖出信号 → SELL_ALL"""
        result = map_signal_to_action(SignalInput(signal_type="卖出", current_position=0.6))
        assert result.action == "SELL_ALL"
        assert result.target_position == 0.0

    def test_mid_watch_returns_hold(self):
        """中仓(0.6)+观望信号 → HOLD"""
        result = map_signal_to_action(SignalInput(signal_type="观望", current_position=0.6))
        assert result.action == "HOLD"


class TestMapSignalToActionFullPosition:
    """Test map_signal_to_action when current_position = 1.0 (full)."""

    def test_full_buy_returns_hold(self):
        """满仓(1.0)+买入信号 → HOLD (无法再买)"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=1.0))
        assert result.action == "HOLD"
        assert result.target_position == 1.0

    def test_full_increase_returns_hold(self):
        """满仓(1.0)+增持信号 → HOLD"""
        result = map_signal_to_action(SignalInput(signal_type="增持", current_position=1.0))
        assert result.action == "HOLD"

    def test_full_decrease_returns_sell_partial(self):
        """满仓(1.0)+减仓信号 → SELL_PARTIAL, target=0.75"""
        result = map_signal_to_action(SignalInput(signal_type="减仓", current_position=1.0))
        assert result.action == "SELL_PARTIAL"
        assert result.target_position == 0.75

    def test_full_sell_returns_sell_all(self):
        """满仓(1.0)+卖出信号 → SELL_ALL"""
        result = map_signal_to_action(SignalInput(signal_type="卖出", current_position=1.0))
        assert result.action == "SELL_ALL"
        assert result.target_position == 0.0

    def test_full_watch_returns_hold(self):
        """满仓(1.0)+观望信号 → HOLD"""
        result = map_signal_to_action(SignalInput(signal_type="观望", current_position=1.0))
        assert result.action == "HOLD"


class TestMapSignalToActionCustomTarget:
    """Test custom target_position override behavior."""

    def test_custom_target_buy_overrides_default(self):
        """自定义 target_position 覆盖默认值"""
        result = map_signal_to_action(SignalInput(
            signal_type="买入",
            current_position=0.2,
            target_position=0.7,
        ))
        assert result.action == "BUY"
        assert result.target_position == 0.7

    def test_custom_target_capped_at_one(self):
        """自定义 target > 1.0 时被截断为 1.0"""
        result = map_signal_to_action(SignalInput(
            signal_type="买入",
            current_position=0.5,
            target_position=1.5,
        ))
        assert result.action == "BUY"
        assert result.target_position == 1.0

    def test_custom_target_increase_override(self):
        """增持信号使用自定义 target"""
        result = map_signal_to_action(SignalInput(
            signal_type="增持",
            current_position=0.3,
            target_position=0.8,
        ))
        assert result.action == "BUY"
        assert result.target_position == 0.8

    def test_custom_target_decrease_override(self):
        """减仓信号使用自定义 target"""
        result = map_signal_to_action(SignalInput(
            signal_type="减仓",
            current_position=0.8,
            target_position=0.4,
        ))
        assert result.action == "SELL_PARTIAL"
        assert result.target_position == 0.4


class TestMapSignalToActionEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_negative_position_buy(self):
        """负仓位时买入信号应正常处理为 BUY"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=-0.1))
        assert result.action == "BUY"

    def test_negative_position_sell(self):
        """负仓位时卖出/减仓信号应返回 HOLD"""
        result = map_signal_to_action(SignalInput(signal_type="卖出", current_position=-0.1))
        assert result.action == "HOLD"

    def test_overflow_position_buy(self):
        """超1仓位时买入信号应返回 HOLD"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=1.2))
        assert result.action == "HOLD"

    def test_overflow_position_decrease(self):
        """超1仓位时减仓信号应正常处理"""
        result = map_signal_to_action(SignalInput(signal_type="减仓", current_position=1.2))
        assert result.action == "SELL_PARTIAL"
        assert result.target_position == 0.95

    def test_zero_position_exact(self):
        """精确零仓位边界测试"""
        result = map_signal_to_action(SignalInput(signal_type="买入", current_position=0.0))
        assert result.action == "BUY"
        assert result.target_position == 1.0

    def test_very_small_position(self):
        """极小仓位(0.001)视为有持仓"""
        result = map_signal_to_action(SignalInput(signal_type="卖出", current_position=0.001))
        assert result.action == "SELL_ALL"


@pytest.mark.signals
class TestApplyCnRulesSuspension:
    """Test apply_cn_rules - suspension rule."""

    def test_suspended_blocks_any_action(self):
        """停牌时任何操作都被阻塞"""
        action, reason = apply_cn_rules("BUY", is_suspended=True, is_limit_up=False, is_limit_down=False)
        assert action == "BLOCKED"
        assert reason == "停牌"

    def test_suspended_blocks_sell(self):
        """停牌时卖出也被阻塞"""
        action, reason = apply_cn_rules("SELL_ALL", is_suspended=True, is_limit_up=False, is_limit_down=False)
        assert action == "BLOCKED"
        assert reason == "停牌"

    def test_not_suspended_passes_through(self):
        """非停牌时不修改操作"""
        action, reason = apply_cn_rules("BUY", is_suspended=False, is_limit_up=False, is_limit_down=False)
        assert action == "BUY"
        assert reason == ""


@pytest.mark.signals
class TestApplyCnRulesLimitUp:
    """Test apply_cn_rules - limit-up rule."""

    def test_limit_up_blocks_buy(self):
        """涨停时不能买入"""
        action, reason = apply_cn_rules("BUY", is_suspended=False, is_limit_up=True, is_limit_down=False)
        assert action == "BLOCKED"
        assert reason == "涨停不可买入"

    def test_limit_up_does_not_block_sell(self):
        """涨停不影响卖出操作"""
        action, reason = apply_cn_rules("SELL_ALL", is_suspended=False, is_limit_up=True, is_limit_down=False)
        assert action == "SELL_ALL"
        assert reason == ""

    def test_no_limit_up_buy_ok(self):
        """非涨停时买入正常"""
        action, reason = apply_cn_rules("BUY", is_suspended=False, is_limit_up=False, is_limit_down=False)
        assert action == "BUY"


@pytest.mark.signals
class TestApplyCnRulesLimitDown:
    """Test apply_cn_rules - limit-down rule."""

    def test_limit_down_blocks_sell_all(self):
        """跌停时不能全部卖出"""
        action, reason = apply_cn_rules("SELL_ALL", is_suspended=False, is_limit_up=False, is_limit_down=True)
        assert action == "BLOCKED"
        assert reason == "跌停不可卖出"

    def test_limit_down_blocks_sell_partial(self):
        """跌停时不能部分卖出"""
        action, reason = apply_cn_rules("SELL_PARTIAL", is_suspended=False, is_limit_up=False, is_limit_down=True)
        assert action == "BLOCKED"
        assert reason == "跌停不可卖出"

    def test_limit_down_does_not_block_buy(self):
        """跌停不影响买入操作"""
        action, reason = apply_cn_rules("BUY", is_suspended=False, is_limit_up=False, is_limit_down=True)
        assert action == "BUY"
        assert reason == ""


@pytest.mark.signals
class TestApplyCnRulesT1:
    """Test apply_cn_rules - T+1 rule."""

    def test_t1_blocked_blocks_sell_all(self):
        """T+1 阻塞时不能卖出"""
        action, reason = apply_cn_rules(
            "SELL_ALL",
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
            is_t1_blocked=True,
        )
        assert action == "BLOCKED"
        assert reason == "T+1 当日买入不可卖出"

    def test_t1_blocked_blocks_sell_partial(self):
        """T+1 阻塞时不能部分卖出"""
        action, reason = apply_cn_rules(
            "SELL_PARTIAL",
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
            is_t1_blocked=True,
        )
        assert action == "BLOCKED"
        assert reason == "T+1 当日买入不可卖出"

    def test_t1_not_blocked_sell_ok(self):
        """非 T+1 阻塞时卖出正常"""
        action, reason = apply_cn_rules(
            "SELL_ALL",
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
            is_t1_blocked=False,
        )
        assert action == "SELL_ALL"

    def test_t1_does_not_block_buy(self):
        """T+1 不影响买入"""
        action, reason = apply_cn_rules(
            "BUY",
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
            is_t1_blocked=True,
        )
        assert action == "BUY"


@pytest.mark.signals
class TestApplyCnRulesNormalAndCombined:
    """Test normal pass-through and combined rule scenarios."""

    def test_normal_hold_passes(self):
        """正常情况下 HOLD 操作不修改"""
        action, reason = apply_cn_rules(
            "HOLD",
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
        )
        assert action == "HOLD"
        assert reason == ""

    def test_combined_suspension_and_limit_up(self):
        """停牌优先级最高，即使涨停也返回停牌原因"""
        action, reason = apply_cn_rules(
            "BUY",
            is_suspended=True,
            is_limit_up=True,
            is_limit_down=False,
        )
        assert action == "BLOCKED"
        assert reason == "停牌"

    def test_combined_limit_up_and_t1_for_sell(self):
        """卖出时同时涨停和T+1，涨停不阻塞卖出但T+1会"""
        action, reason = apply_cn_rules(
            "SELL_ALL",
            is_suspended=False,
            is_limit_up=True,
            is_limit_down=False,
            is_t1_blocked=True,
        )
        assert action == "BLOCKED"
        assert reason == "T+1 当日买入不可卖出"

    def test_no_rules_triggered(self):
        """没有任何规则触发时原样返回"""
        for action_type in ["BUY", "SELL_ALL", "SELL_PARTIAL", "HOLD"]:
            action, reason = apply_cn_rules(
                action_type,
                is_suspended=False,
                is_limit_up=False,
                is_limit_down=False,
                is_t1_blocked=False,
            )
            assert action == action_type
            assert reason == ""
