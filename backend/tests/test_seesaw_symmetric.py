"""P2 退出对称化：classify_market_state 对称行为测试。

核心回归点：此前进入 down 只需 4 个弱条件之一，但离开 down 的“up”需三重严格确认
（收盘>MA20 且 MA5>MA20 且 收盘>MA60），即**进敏退钝**。大跌后即便反弹，价格仍长期
低于被砸低的双均线、且距前高跌幅（drop_from_high）持续为负，于是被持续判为 down、
踏空反弹。

对称模式让退出 down 与进入镜像：价格涨破任一均线 / 单日反弹 / 自低点回升 / 金叉 即释放。
"""
from __future__ import annotations

import numpy as np

from app.data.seesaw import DefensiveRules, classify_market_state


def _series(prices: list[float]) -> "np.ndarray":
    return np.array(prices, dtype=float)


def test_symmetric_flag_default_true():
    # 默认开启对称（新行为）；False 用于复现 #206 基线
    assert DefensiveRules().symmetric_recovery is True
    assert DefensiveRules(symmetric_recovery=False).symmetric_recovery is False


def test_symmetric_crash_triggers_down():
    # 长期上行后暴跌，收盘跌破双均线 → down（与旧行为一致，进入路径不变）
    prices = list(np.linspace(100, 130, 60)) + list(np.linspace(130, 90, 10))
    state, detail = classify_market_state(_series(prices), DefensiveRules())
    assert state == "down"
    assert any("price_below_ma20_and_ma60" in c for c in detail["down_conditions"])


def test_symmetric_rebound_exits_down_not_stuck():
    """关键回归：平台 → 尖顶 → 崩塌 → 反弹。

    构造：60 天 100 平台、尖顶 120、崩至 85、反弹至 105。
    - 旧行为（symmetric_recovery=False）：距前高跌幅（drop_from_high）滞后为负 +
      还要求 MA5>MA20 才判 up → 持续判 down（踏空反弹）。
    - 对称行为（默认）：价格重回双均线上方（结构位）即判 up，释放 down。
    """
    prices = [100.0] * 60 + [120.0, 95.0, 88.0, 85.0, 105.0]
    s_sym, d_sym = classify_market_state(_series(prices), DefensiveRules(symmetric_recovery=True))
    s_old, _ = classify_market_state(_series(prices), DefensiveRules(symmetric_recovery=False))
    assert s_old == "down"                          # 旧行为：滞后锁死
    assert s_sym == "up"                            # 对称：反弹即释放
    assert "price_above_ma20_and_ma60" in d_sym["up_conditions"]


def test_old_behavior_drop_from_high_locks_down():
    # 显式锁定旧分支基线行为：距前高深跌 + 单日反弹仍处低位 → down
    prices = (
        list(np.linspace(100, 130, 60))
        + list(np.linspace(130, 90, 10))
        + [105.0]
    )
    state, _ = classify_market_state(_series(prices), DefensiveRules(symmetric_recovery=False))
    assert state == "down"


def test_symmetric_full_recovery_not_stuck():
    # 连续反弹把价格拉回双均线上方区域 → 不卡在 down（允许 up 或 neutral）
    base = list(np.linspace(100, 130, 60)) + list(np.linspace(130, 90, 10))
    rebound = list(np.linspace(90, 125, 15))
    prices = base + rebound
    state, _ = classify_market_state(_series(prices), DefensiveRules(symmetric_recovery=True))
    assert state != "down"


def test_symmetric_uptrend_returns_up():
    # 明确上行趋势 → up（对称看涨信号，结构位确认）
    prices = list(np.linspace(100, 160, 80))
    state, detail = classify_market_state(_series(prices), DefensiveRules(symmetric_recovery=True))
    assert state == "up"
    assert "price_above_ma20_and_ma60" in detail["up_conditions"]
