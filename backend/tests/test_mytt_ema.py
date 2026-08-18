"""Phase 2 回归：MyTT.EMA / MACD 的 numpy 实现必须与 pandas ewm(adjust=False) 逐位一致。

Phase 2 把 EMA 从 ``pd.Series(S).ewm(span=N, adjust=False)`` 换成纯 numpy 递推 +
逐股整段缓存（视图切片走缓存，再用初值差闭式修正还原"窗口起点重新播种"语义）。
这一步是提速核心，但极易引入 warmup 偏差（N=26 的长 EMA 在 60 根窗口里会被静默偏移
~1%，可能翻转边际金叉信号）。本测试用数值对拍把"零偏差"钉死。

若将来有人改 EMA/MACD 实现，本文件必须保持 max abs diff 在机器精度量级。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.libs.MyTT import EMA, MACD, clear_ema_cache


def _pandas_ema(S, N):
    return pd.Series(np.asarray(S, dtype=float)).ewm(span=N, adjust=False).mean().values


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 123])
def test_ema_matches_pandas_adjust_false(seed):
    rng = np.random.default_rng(seed)
    max_err = 0.0
    for _ in range(40):
        n = int(rng.integers(10, 400))
        S = rng.normal(0, 1, size=n)
        N = int(rng.integers(3, 60))
        new = EMA(S, N)
        ref = _pandas_ema(S, N)
        assert new.shape == ref.shape
        max_err = max(max_err, float(np.max(np.abs(new - ref))))
    assert max_err < 1e-10, f"EMA 与 pandas 偏差过大: {max_err}"


def test_ema_view_cache_path_equals_direct_and_pandas():
    """视图切片（回测热路径）必须与直接调用、pandas 结果三者一致。"""
    rng = np.random.default_rng(5)
    base = rng.normal(0, 1, size=500)
    clear_ema_cache()
    for _ in range(30):
        lo = int(rng.integers(0, 420))
        hi = int(rng.integers(lo + 5, 500))
        arr = base[lo:hi]  # 视图：base 即 _StockArrays.close 的模拟
        a_view = EMA(arr, 26)
        a_direct = EMA(np.asarray(arr, dtype=np.float64), 26)  # base=None -> 直接路径
        a_pandas = _pandas_ema(arr, 26)
        assert np.allclose(a_view, a_direct, atol=1e-12)
        assert np.allclose(a_view, a_pandas, atol=1e-10), (
            f"视图缓存路径与 pandas 不一致 @[{lo}:{hi}]"
        )
    clear_ema_cache()


@pytest.mark.parametrize("N", [5, 12, 26, 60])
def test_ema_short_window_no_warmup_bias(N):
    """关键：长 EMA 在非 0 起点的窗口上不能出现 warmup 偏差。"""
    rng = np.random.default_rng(99)
    base = rng.normal(0, 1, size=600)
    for start in (0, 30, 120, 300, 540):
        win = base[start : start + 60]
        assert np.allclose(EMA(win, N), _pandas_ema(win, N), atol=1e-10)


def test_macd_end_to_end_matches_pandas_reference():
    """MACD 是 EMA 的线性组合，EMA 等价则 MACD 等价——端到端钉死。"""
    rng = np.random.default_rng(2024)
    max_err = 0.0
    for _ in range(50):
        n = int(rng.integers(35, 500))
        close = rng.normal(10, 0.5, size=n)
        # 新实现
        dif, dea, macd = MACD(close)
        # pandas 参考（用 pandas EMA 重算 MACD 公式）
        e12 = _pandas_ema(close, 12)
        e26 = _pandas_ema(close, 26)
        rd = e12 - e26
        dea_ref = _pandas_ema(rd, 9)
        macd_ref = (rd - dea_ref) * 2
        for got, ref in ((dif, np.round(rd, 3)), (dea, np.round(dea_ref, 3)),
                         (macd, np.round(macd_ref, 3))):
            assert got.shape == ref.shape
            max_err = max(max_err, float(np.max(np.abs(got - ref))))
    assert max_err < 1e-9, f"MACD 与 pandas 参考偏差过大: {max_err}"
