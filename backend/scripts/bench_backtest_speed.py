"""回测引擎速度基准（Phase 0）。

目的：在改 Phase 1（每股票 numpy 预计算）之前/之后，给出可复现的墙钟数字，
证明优化的收益，避免拍脑袋。

用法（在 backend/ 目录下，用项目 venv）：
    .venv/bin/python scripts/bench_backtest_speed.py                 # 默认 300 股 × 1000 日 合成
    .venv/bin/python scripts/bench_backtest_speed.py --stocks 2000 --days 2500
    .venv/bin/python scripts/bench_backtest_speed.py --real --stocks 800 --start 2020-01-01 --end 2023-12-31

默认合成模式：内存随机游走生成 OHLCV，不依赖数据库，结果可复现（--seed）。
--real 模式：从本地 leek-quant 库 daily_kline 加载真实数据（需本地 Postgres 就绪）。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

# 允许以脚本方式从 backend/ 运行
sys.path.insert(0, "")

from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar  # noqa: E402


# ---------------------------------------------------------------------------
# 策略：MACD 金叉/死叉（必触发 ctx.close 等数组访问，正好压中 Phase 1 的瓶颈）
# ---------------------------------------------------------------------------
STRATEGY_SOURCE = '''
def generate_signal(ctx):
    close = ctx.close
    if len(close) < 35:
        return None
    dif, dea, macd = MACD(close)
    if len(dif) < 2:
        return None
    if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
        return {"signal_type": "买入", "target_position": 1.0, "reason": "MACD金叉"}
    if dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
        return {"signal_type": "卖出", "target_position": 0.0, "reason": "MACD死叉"}
    return None
'''


def _weekday_calendar(n_days: int, start: date) -> list[date]:
    cal: list[date] = []
    d = start
    while len(cal) < n_days:
        if d.weekday() < 5:  # 周一~周五
            cal.append(d)
        d += timedelta(days=1)
    return cal


def gen_synthetic_klines(n_stocks: int, n_days: int, seed: int) -> tuple[dict[str, list[KBar]], date, date]:
    import numpy as np

    rng = np.random.default_rng(seed)
    cal = _weekday_calendar(n_days, date(2019, 1, 1))
    codes = [f"{i:06d}.SZ" for i in range(n_stocks)]

    # 向量化生成价格序列（仅构造阶段，不计入 run() 计时）
    rets = rng.normal(0.0, 0.02, size=(n_stocks, n_days))
    price = 10.0 * np.cumprod(1.0 + rets, axis=1)
    open_ = price * (1.0 + rng.normal(0.0, 0.003, size=(n_stocks, n_days)))
    high = np.maximum(price, open_) * (1.0 + np.abs(rng.normal(0.0, 0.005, size=(n_stocks, n_days))))
    low = np.minimum(price, open_) * (1.0 - np.abs(rng.normal(0.0, 0.005, size=(n_stocks, n_days))))
    volume = rng.integers(100_000, 1_000_000, size=(n_stocks, n_days)).astype("int64")

    all_klines: dict[str, list[KBar]] = {}
    for s in range(n_stocks):
        kbars: list[KBar] = []
        for i in range(n_days):
            close_i = float(price[s, i])
            pre = float(price[s, i - 1]) if i > 0 else close_i
            kbars.append(
                KBar(
                    ts_code=codes[s],
                    trade_date=cal[i],
                    open=Decimal(str(float(open_[s, i]))),
                    high=Decimal(str(float(high[s, i]))),
                    low=Decimal(str(float(low[s, i]))),
                    close=Decimal(str(close_i)),
                    pre_close=Decimal(str(pre)),
                    volume=int(volume[s, i]),
                    amount=Decimal(str(float(volume[s, i]) * close_i)),
                    turnover_rate=None,
                    adj_factor=None,
                    is_suspended=False,
                    is_limit_up=False,
                    is_limit_down=False,
                )
            )
        all_klines[codes[s]] = kbars

    return all_klines, cal[0], cal[-1]


async def load_real_klines(n_stocks: int, start: date, end: date) -> tuple[dict[str, list[KBar]], date, date]:
    from app.core.config import get_settings
    from app.data.repository.kline import (  # type: ignore
        get_kline_rows_for_codes,
    )

    settings = get_settings()
    # 复用回测任务的 bulk 查询思路
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.database_url, future=True)
    from app.core.database import async_session_factory  # 优先用项目 session 工厂

    async with async_session_factory() as session:
        from app.backtest.tasks import _parse_kline_rows

        # 取区间内有数据的前 N 只股票
        res = await session.execute(
            text(
                "SELECT DISTINCT ts_code FROM daily_kline "
                "WHERE trade_date BETWEEN :s AND :e ORDER BY ts_code LIMIT :n"
            ),
            {"s": start, "e": end, "n": n_stocks},
        )
        codes = [r["ts_code"] for r in res.mappings().all()]
        if not codes:
            raise RuntimeError("本地库 daily_kline 在指定区间无数据")
        res = await session.execute(
            text(
                "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
                "volume, amount, turnover_rate, adj_factor, is_suspended, "
                "is_limit_up, is_limit_down FROM daily_kline "
                "WHERE ts_code = ANY(CAST(:codes AS VARCHAR[])) AND trade_date BETWEEN :s AND :e "
                "ORDER BY ts_code, trade_date"
            ),
            {"codes": codes, "s": start, "e": end},
        )
        raw: dict[str, list[dict[str, Any]]] = {}
        for row in res.mappings().all():
            d = dict(row)
            raw.setdefault(d["ts_code"], []).append(d)
        all_klines = {c: _parse_kline_rows(rows) for c, rows in raw.items()}
    await engine.dispose()
    return all_klines, start, end


def run_benchmark(all_klines: dict[str, list[KBar]], start: date, end: date) -> dict[str, Any]:
    config = BacktestConfig(
        strategy_id=1,
        source_code=STRATEGY_SOURCE,
        stock_pool=list(all_klines.keys()),
        start_date=start,
        end_date=end,
        initial_cash=Decimal("100000"),
    )
    runner = BacktestRunner(config)

    # 预热：首次编译策略 + 首次 numpy 路径，排除冷启动噪声
    runner.run({c: k[: min(80, len(k))] for c, k in all_klines.items()})

    t0 = time.perf_counter()
    results = runner.run(all_klines)
    elapsed = time.perf_counter() - t0

    total_bars = sum(len(k) for k in all_klines.values())
    n_stocks = len(all_klines)
    n_dates = len({k.trade_date for k in all_klines.values() for k in all_klines[k.ts_code]}) if False else None
    # 用引擎内实际交易日数
    trading_dates = runner._get_trading_dates(all_klines)
    iterations = len(trading_dates) * n_stocks

    stats = {
        "stocks": n_stocks,
        "trading_dates": len(trading_dates),
        "total_bars": total_bars,
        "iterations": iterations,
        "elapsed_s": elapsed,
        "ms_per_1k_iter": (elapsed / iterations) * 1000 if iterations else 0.0,
        "trades": len(results.get("trades", [])),
        "signals": len(results.get("signal_log", [])),
    }
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=100)
    ap.add_argument("--days", type=int, default=600)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--real", action="store_true", help="从本地库加载真实数据")
    ap.add_argument("--start", type=str, default="2020-01-01")
    ap.add_argument("--end", type=str, default="2023-12-31")
    args = ap.parse_args()

    print("=" * 64)
    print("回测引擎速度基准 (Phase 0)")
    print("=" * 64)

    if args.real:
        import asyncio

        print(f"模式: 真实数据 (本地库)  start={args.start} end={args.end} limit={args.stocks}")
        all_klines, start, end = asyncio.run(
            load_real_klines(args.stocks, date.fromisoformat(args.start), date.fromisoformat(args.end))
        )
    else:
        print(f"模式: 合成数据  stocks={args.stocks} days={args.days} seed={args.seed}")
        all_klines, start, end = gen_synthetic_klines(args.stocks, args.days, args.seed)

    print(f"加载完成: {len(all_klines)} 只股票, 区间 {start} ~ {end}")
    stats = run_benchmark(all_klines, start, end)
    print("-" * 64)
    print(f"股票数        : {stats['stocks']}")
    print(f"交易日数      : {stats['trading_dates']}")
    print(f"K线总根数     : {stats['total_bars']}")
    print(f"主循环迭代数  : {stats['iterations']}  (交易日 × 股票)")
    print(f"run() 耗时    : {stats['elapsed_s']:.3f} s")
    print(f"每千次迭代    : {stats['ms_per_1k_iter']:.3f} ms")
    print(f"成交笔数      : {stats['trades']}")
    print(f"信号数        : {stats['signals']}")
    print("=" * 64)


if __name__ == "__main__":
    main()
