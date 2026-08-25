"""Tests for the backtest-engine defensive switch (跷跷板) overlay.

Covers:
- Benchmark-weak -> force reallocate from strategy to defensive pool (no lookahead)
- Benchmark recovery -> resume strategy
- `performance.defensive` / `defensive` stats (episodes, return_pct, contribution_pct)
- equity_curve `defensive` flag marks the risk-off window
- Disabled switch produces no episodes / zero defensive return
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.backtest import adapter as adapter_module
from app.backtest.adapter import (
    BacktestConfig,
    BacktestContext,
    BacktestRunner,
    KBar,
    TradeRecord,
)
from app.backtest.strategy_runtime import StrategyExecutionResult


@pytest.fixture(autouse=True)
def patch_backtest_context_position_setter(monkeypatch):
    """Mirror test_adapter.py workaround: add current_position setter + fake executor."""
    original_current_position = BacktestContext.current_position
    _position_value = 0.0

    def getter(self):
        return _position_value

    def setter(self, value):
        nonlocal _position_value
        _position_value = value

    def fake_execute_strategy(compiled, ctx, **_kwargs):
        try:
            func = compiled.get("generate_signal") if isinstance(compiled, dict) else None
            if func is None:
                return StrategyExecutionResult(ok=True, signal=None)
            result = func(ctx)
            return StrategyExecutionResult(ok=True, signal=result if isinstance(result, dict) else None)
        except Exception as exc:
            return StrategyExecutionResult(
                ok=False,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
                traceback="test traceback",
            )

    BacktestContext.current_position = property(getter, setter)
    monkeypatch.setattr(adapter_module, "execute_compiled_signal", fake_execute_strategy)
    yield
    BacktestContext.current_position = original_current_position


ALWAYS_BUY_STRATEGY = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 0.8}
'''


def _make_klines(ts_code: str, closes, start: date) -> list[KBar]:
    """Build KBar list from a sequence of close prices (one bar per calendar day)."""
    klines: list[KBar] = []
    pre = Decimal(str(closes[0]))
    for i, c in enumerate(closes):
        c = Decimal(str(c))
        klines.append(KBar(
            ts_code=ts_code,
            trade_date=start + timedelta(days=i),
            open=c - Decimal("0.1"),
            high=c + Decimal("0.2"),
            low=c - Decimal("0.2"),
            close=c,
            pre_close=pre,
            volume=1_000_000,
            amount=c * 1_000_000,
            adj_factor=None,
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
            turnover_rate=None,
        ))
        pre = c
    return klines


def _benchmark_closes(n: int = 60):
    """Up-trend for 30 days, sharp crash on day 30, weak for ~19 days, recovery day 50+."""
    closes = [1000 + i * 10 for i in range(30)]   # 1000 .. 1290
    closes.append(1196)                            # day 30: ~ -7.3% crash -> "down"
    for i in range(19):                            # days 31..49 drift lower
        closes.append(1196 - i * 2)                # 1196 .. 1158
    closes.append(1300)                            # day 50: recovery above MA20
    for i in range(9):                             # days 51..59
        closes.append(1300 + (i + 1) * 10)
    assert len(closes) == n
    return closes


def _build_inputs(start: date):
    strat_k = _make_klines("000001.SZ", [10 + i * 0.3 for i in range(60)], start)
    def1_k = _make_klines("DEF1.SH", [100 + i * 0.83 for i in range(60)], start)
    def2_k = _make_klines("DEF2.SH", [100 + i * 0.90 for i in range(60)], start)
    bm_k = _make_klines("000300.SH", _benchmark_closes(), start)
    all_klines = {
        "000001.SZ": strat_k,
        "DEF1.SH": def1_k,
        "DEF2.SH": def2_k,
    }
    defensive_klines = {"DEF1.SH": def1_k, "DEF2.SH": def2_k}
    return all_klines, defensive_klines, bm_k


def _run(defensive_enabled: bool, start: date):
    all_klines, defensive_klines, bm_k = _build_inputs(start)
    config = BacktestConfig(
        strategy_id=1,
        source_code=ALWAYS_BUY_STRATEGY,
        stock_pool=["000001.SZ"],
        start_date=start,
        end_date=start + timedelta(days=59),
        initial_cash=Decimal("100000"),
        # 避险切换（跷跷板）
        defensive_switch_enabled=defensive_enabled,
        defensive_pool_codes=["DEF1.SH", "DEF2.SH"],
        defensive_pick_k=2,
        defensive_benchmark_code="000300.SH",
        defensive_rules={},
    )
    runner = BacktestRunner(config)
    return runner.run(
        all_klines,
        benchmark_klines=bm_k,
        defensive_klines=defensive_klines,
        defensive_benchmark_klines=bm_k,
    )


# ── 测试 ───────────────────────────────────────────────────────────────────────


def test_defensive_switch_active_produces_episodes_and_metrics():
    """启用时：基准走弱触发切换，产生分段收益与避险收益统计。"""
    start = date(2026, 5, 1)
    result = _run(True, start)

    d = result["defensive"]
    assert d["enabled"] is True
    assert d["active"] is True
    assert d["periods"] >= 1, "should record at least one defensive episode"
    assert d["detail"], "detail should list episode entry/exit"
    ep = d["detail"][0]
    assert "entry_date" in ep and "exit_date" in ep and "return_pct" in ep
    assert ep["holdings"] == ["DEF1.SH", "DEF2.SH"]

    # 避险收益指标（链式累计 + 对总收益贡献）应为有限数
    assert isinstance(d["return_pct"], (int, float))
    assert d["return_pct"] is not None
    assert isinstance(d["contribution_pct"], (int, float))
    # 基准走弱期间避险股上行 -> 避险收益应为正
    assert d["return_pct"] > 0, f"expected positive defensive return, got {d['return_pct']}"

    # 最终应回到常规模式（基准已收复）
    assert d["final_mode"] == "normal"

    # performance 镜像同一份统计
    assert result["performance"]["defensive"]["return_pct"] == d["return_pct"]


def test_defensive_switch_equity_flags_and_signals():
    """启用时：equity_curve 标记风险窗口，signal_log 含切换/避险买入信号。"""
    start = date(2026, 5, 1)
    result = _run(True, start)

    flags = [e["defensive"] for e in result["equity_curve"]]
    assert True in flags, "risk-off window should be flagged"
    assert False in flags, "normal window should also be flagged"

    sig_types = [s["signal_type"] for s in result["signal_log"]]
    assert "避险切换" in sig_types, "should log the force-switch-out signal"
    assert "避险买入" in sig_types, "should log defensive allocation buys"

    # trade_records 含清仓(避险切换)与避险买入
    assert any(t["exit_reason"] == "避险切换" for t in result["trade_records"])
    assert any(t["signal_reason"] == "基准走弱-配置避险库" for t in result["trade_records"])


def test_defensive_switch_disabled_no_episodes():
    """禁用时：不产生任何切换，避险收益统计为零、无明细。"""
    start = date(2026, 5, 1)
    result = _run(False, start)

    d = result["defensive"]
    assert d["enabled"] is False
    assert d["active"] is False
    assert d["periods"] == 0
    assert d["detail"] == []
    assert d["return_pct"] == 0.0

    flags = [e["defensive"] for e in result["equity_curve"]]
    assert True not in flags, "disabled switch must never flag risk-off"


def test_defensive_switch_inactive_without_pool():
    """启用但避险库为空：优雅降级，不切换、不产生明细（无异常）。"""
    start = date(2026, 5, 1)
    all_klines, _def_klines, bm_k = _build_inputs(start)
    config = BacktestConfig(
        strategy_id=1,
        source_code=ALWAYS_BUY_STRATEGY,
        stock_pool=["000001.SZ"],
        start_date=start,
        end_date=start + timedelta(days=59),
        initial_cash=Decimal("100000"),
        defensive_switch_enabled=True,
        defensive_pool_codes=[],          # 空池 -> 应降级
        defensive_pick_k=2,
        defensive_benchmark_code="000300.SH",
        defensive_rules={},
    )
    runner = BacktestRunner(config)
    # 不传入 defensive_klines，模拟库为空
    result = runner.run(all_klines, benchmark_klines=bm_k, defensive_klines={}, defensive_benchmark_klines=bm_k)

    d = result["defensive"]
    assert d["enabled"] is True
    assert d["active"] is False, "empty pool must disable the overlay"
    assert d["periods"] == 0
    assert all(not e["defensive"] for e in result["equity_curve"])


def test_defensive_switch_no_daily_whipsaw_in_sustained_downtrend():
    """回归：连续走弱窗口只产生一段避险 episode，而非每个交易日一条（防 whipsaw）。

    历史 Bug：_defensive_mode 为布尔，却被拿来与字符串 "defensive"/"normal" 比较，
    翻转守卫永不触发 -> 每个交易日都全量再平衡并追加一条未关闭的 episode
    （真实回测 #312：163 条重叠 episode、3335 笔成交、return_pct=-100%、
    contribution_pct=-1889%）。修复后 _benchmark_closes() 的 19 天连续走弱
    窗口应只产生 1 段 episode，避险切换清仓成交有界。
    """
    start = date(2026, 5, 1)
    result = _run(True, start)

    d = result["defensive"]
    assert d["enabled"] is True and d["active"] is True

    # 连续走弱（19 天）只应记 1 段避险 episode
    assert d["periods"] == 1, (
        f"expected 1 defensive episode for a sustained downtrend, got {d['periods']}"
    )
    assert d["days"] >= 15, f"defensive days should cover the weak window, got {d['days']}"

    # 避险切换清仓应只发生在入场/离场两次翻转，而非每天一次
    switch_sells = [t for t in result["trade_records"] if t["exit_reason"] == "避险切换"]
    assert len(switch_sells) <= 6, f"whipsaw: too many 避险切换 sells ({len(switch_sells)})"

    # 避险收益为有限合理值（非 -100% / 荒谬贡献）
    assert -100.0 < d["return_pct"] < 100.0, f"suspicious defensive return_pct: {d['return_pct']}"
    assert -1000.0 < d["contribution_pct"] < 1000.0, (
        f"suspicious defensive contribution_pct: {d['contribution_pct']}"
    )
