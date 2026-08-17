"""Tests for the MyTT indicator library (third_party/libs/MyTT.py).

MyTT is the foundation of all signal calculation. A silent bug here would
corrupt every signal, so it must be pinned with known-good reference values
plus property tests (length preservation, no-throw on degenerate series).

The module is loaded directly from third_party/libs because it is imported
dynamically by the strategy sandbox, not via the app package.
"""
import os
import sys

import numpy as np
import pytest

_THIRD_PARTY_LIBS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "third_party", "libs")
)
if _THIRD_PARTY_LIBS not in sys.path:
    sys.path.insert(0, _THIRD_PARTY_LIBS)

import MyTT  # noqa: E402

pytestmark = pytest.mark.myt


# ---------------------------------------------------------------- MA / EMA
def test_MA_rolling_mean_reference():
    c = [1, 2, 3, 4, 5]
    out = MyTT.MA(c, 3)
    # rolling mean: first two are NaN, then (1+2+3)/3=2, 3, 4
    assert np.isnan(out[0])
    assert np.isnan(out[1])
    assert np.allclose(out[2:], [2.0, 3.0, 4.0])


def test_EMA_matches_closed_form():
    c = [1.0, 2.0, 3.0, 4.0, 5.0]
    n = 3
    alpha = 2.0 / (n + 1)
    expected = [c[0]]
    for v in c[1:]:
        expected.append(alpha * v + (1 - alpha) * expected[-1])
    out = MyTT.EMA(c, n)
    assert np.allclose(out, expected, rtol=1e-9)


def test_EMA_length_preserved():
    c = np.arange(1, 21, dtype=float)
    assert len(MyTT.EMA(c, 12)) == len(c)


# ---------------------------------------------------------------- RSI
def test_RSI_known_series():
    # 6 periods. MyTT.RSI uses an EMA-style SMA (alpha = 1/N), recomputed
    # independently here so the assertion is a true cross-check.
    c = [1, 2, 3, 1, 2, 3]
    out = MyTT.RSI(c, 3)

    import pandas as pd

    def _ema_sma(s, n):
        return pd.Series(s).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()

    diffs = np.diff(c)
    gains = np.maximum(diffs, 0.0)
    losses = np.maximum(-diffs, 0.0)
    avg_gain = _ema_sma(gains, 3)[-1]
    avg_loss = _ema_sma(losses, 3)[-1]
    expected = 100 * avg_gain / (avg_gain + avg_loss)
    assert np.isclose(out[-1], expected, atol=1e-2)


def test_RSI_bounds_and_all_one_direction():
    up = np.arange(1, 30, dtype=float)        # strictly increasing -> RSI 100
    down = np.arange(30, 1, -1, dtype=float)  # strictly decreasing -> RSI 0
    assert np.allclose(MyTT.RSI(up, 14)[-1], 100.0, atol=1e-6)
    assert np.allclose(MyTT.RSI(down, 14)[-1], 0.0, atol=1e-6)


# ---------------------------------------------------------------- MACD
def test_MACD_returns_three_equal_length_arrays():
    c = np.sin(np.linspace(0, 20, 120)) + 5
    dif, dea, macd = MyTT.MACD(c)
    assert len(dif) == len(dea) == len(macd) == len(c)
    # macd must equal (dif - dea) * 2 (within rounding to 3 decimals)
    assert np.allclose(MyTT.RD(macd), MyTT.RD((dif - dea) * 2), atol=1e-2)


def test_MACD_zero_series_no_nan():
    c = np.zeros(120)
    dif, dea, macd = MyTT.MACD(c)
    assert not np.any(np.isnan(dif))
    assert np.allclose(dif, 0.0, atol=1e-9)


# ---------------------------------------------------------------- KDJ
def test_KDJ_length_and_ordering():
    n = 60
    close = np.sin(np.linspace(0, 10, n)) * 5 + 10
    # Keep high strictly above low so the RSV denominator is never zero
    # (MyTT.KDJ returns NaN on a flat high==low range).
    high = close + 1.0
    low = close - 1.0
    k, d, j = MyTT.KDJ(close, high, low)
    assert len(k) == len(d) == len(j) == n
    # J == 3K - 2D. The first N-1 values are NaN (EMA warm-up) on both
    # sides, so compare with equal_nan.
    assert np.allclose(MyTT.RD(j), MyTT.RD(3 * k - 2 * d), equal_nan=True)


# ---------------------------------------------------------------- BOLL
def test_BOLL_band_ordering():
    c = np.linspace(10, 20, 50)
    upper, mid, lower = MyTT.BOLL(c, 20, 2)
    # where defined, upper >= mid >= lower
    mask = ~np.isnan(upper)
    assert np.all(upper[mask] >= mid[mask])
    assert np.all(mid[mask] >= lower[mask])


# ---------------------------------------------------------------- CROSS / REF
def test_CROSS_detects_golden_cross():
    # MyTT.CROSS requires numpy arrays (list comparison short-circuits to a
    # single bool in Python); this also pins that contract.
    a = np.array([1, 1, 1, 2, 3])   # crosses above b on the 4th element
    b = np.array([2, 2, 2, 1, 1])
    out = MyTT.CROSS(a, b)
    # first element is forced False; cross happens at index 3
    assert out[0] == False
    assert out[3] == True
    assert out[4] == False


def test_REF_shifts_and_nans():
    s = [10, 20, 30, 40]
    out = MyTT.REF(s, 1)
    assert np.isnan(out[0])
    assert np.allclose(out[1:], [10, 20, 30])


# ---------------------------------------------------------------- property
@pytest.mark.parametrize(
    "func,args",
    [
        (lambda s: MyTT.MA(s, 5), ()),
        (lambda s: MyTT.EMA(s, 12), ()),
        (lambda s: MyTT.RSI(s, 14), ()),
        (lambda s: MyTT.BOLL(s, 20, 2), ()),
    ],
)
def test_no_throw_on_degenerate_series(func, args):
    """Indicators must not raise on constant / all-up / all-down series."""
    for series in ([5.0] * 30, list(range(30)), list(range(30, 0, -1))):
        result = func(series)
        if isinstance(result, tuple):
            for r in result:
                assert len(r) == len(series)
        else:
            assert len(result) == len(series)
