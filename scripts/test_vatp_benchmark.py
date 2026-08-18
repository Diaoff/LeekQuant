# -*- coding: utf-8 -*-
"""冒烟测试：验证 VATP 策略的 ctx.benchmark 大盘择时闸门。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))

import numpy as np
from decimal import Decimal
from datetime import date, timedelta

from app.backtest.adapter import KBar, _StockArrays, BacktestContext
from app.libs.MyTT import MA, ATR, RSI

import vatp_trend_pullback as vatp

# 注入策略运行所需的指标函数（引擎在编译时自动注入，这里手动补）
vatp.MA, vatp.ATR, vatp.RSI = MA, ATR, RSI

# 把个股过滤放宽，单独验证 benchmark 闸门逻辑
vatp.PARAMS.update({
    "rsi_min": -999, "rsi_max": 999, "atr_pct_max": 999.0,
    "vol_spike_max": 9, "rev_confirm": False, "pullback_pct": 1.0,
    "cooldown_days": 0, "min_up_pct": 0.0,
    "benchmark_trend_filter": True, "benchmark_ma": 20,
    "benchmark_min_up_pct": 0.0, "benchmark_rs_filter": False,
})

N = 65
start = date(2020, 1, 1)
dates = [start + timedelta(days=i) for i in range(N)]

def ramp(up=True):
    base = np.linspace(10.0, 14.0, N) if up else np.linspace(14.0, 10.0, N)
    return base + np.random.RandomState(1).normal(0, 0.05, N)

stock_close = ramp(up=True)
bull_bench = ramp(up=True)
bear_bench = ramp(up=False)  # 大盘空头

def make_klines(closes):
    kl = []
    for i, c in enumerate(closes):
        c = float(c)
        kl.append(KBar(
            ts_code="T", trade_date=dates[i],
            open=Decimal(str(c)), high=Decimal(str(c * 1.01)),
            low=Decimal(str(c * 0.99)), close=Decimal(str(c)),
            pre_close=Decimal(str(c)), volume=1000, amount=Decimal(str(c * 1000)),
            adj_factor=None, is_suspended=False, is_limit_up=False, is_limit_down=False,
        ))
    return kl

stock_arr = _StockArrays.from_klines(make_klines(stock_close))
bull_arr = _StockArrays.from_klines(make_klines(bull_bench))
bear_arr = _StockArrays.from_klines(make_klines(bear_bench))

def run(bench_arrays):
    ctx = BacktestContext.from_arrays(
        stock_arr, idx=N - 1, lookback=60, positions={},
        total_asset=Decimal("1e6"), ts_code="000001.XSHG",
        benchmark_arrays=bench_arrays,
    )
    return vatp.generate_signal(ctx)

sig_bull = run(bull_arr)
sig_bear = run(bear_arr)
sig_none = run(None)

print("大盘多头  ->", sig_bull.get("signal_type"), "| reason:", sig_bull.get("reason", ""))
print("大盘空头  ->", sig_bear.get("signal_type"), "| reason:", sig_bear.get("reason", ""))
print("无benchmark->", sig_none.get("signal_type"), "| reason:", sig_none.get("reason", ""))

assert sig_bear.get("reason", "").startswith("大盘"), "空头场景应被大盘闸门拦下"
assert sig_bull.get("signal_type") == "买入", "多头场景应放行买入"
assert sig_none.get("signal_type") == "买入", "无benchmark应降级放行"
print("\n✅ VATP 大盘择时闸门验证通过")
