"""把主流指数登记进 stock_basic 并拉取日 K 线到 daily_kline。

用法（在 backend/ 目录下用 venv 运行）:
    .venv/bin/python ../scripts/add_index_basics_and_klines.py basic   # 仅登记 stock_basic
    .venv/bin/python ../scripts/add_index_basics_and_klines.py kline   # 仅拉 K 线
    .venv/bin/python ../scripts/add_index_basics_and_klines.py all     # 两者都做

依赖：修改了 app/data/stock_scope.py 的 excluded_stock_sql_condition，使 market='指数'
的行（含 000688.SH 科创50的 688 前缀）整体不被 excluded，从而在 delete_unsupported
维护任务中 daily_kline / stock_basic 都不会被清掉。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

# 必须先加载 .env 再 import app（app.core.config 在导入时读环境变量）
try:
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
except Exception:
    if ENV_PATH.exists():
        for _line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text  # noqa: E402

from app.data.service import sync_one_stock  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402

# (ts_code, symbol, name, exchange)
INDEXES = [
    ("000001.SH", "000001", "上证指数", "SH"),
    ("399001.SZ", "399001", "深证成指", "SZ"),
    ("000300.SH", "000300", "沪深300", "SH"),
    ("000905.SH", "000905", "中证500", "SH"),
    ("000852.SH", "000852", "中证1000", "SH"),
    ("000016.SH", "000016", "上证50", "SH"),
    ("000010.SH", "000010", "上证180", "SH"),
    ("000906.SH", "000906", "中证800", "SH"),
    ("399330.SZ", "399330", "深证100", "SZ"),
    ("399005.SZ", "399005", "中小100", "SZ"),
    ("000688.SH", "000688", "科创50", "SH"),
    ("399006.SZ", "399006", "创业板指", "SZ"),
    ("399673.SZ", "399673", "创业板50", "SZ"),
    ("000015.SH", "000015", "上证红利", "SH"),
    ("000922.SH", "000922", "中证红利", "SH"),
    ("399371.SZ", "399371", "国证2000", "SZ"),
]

KLINE_START = date(2015, 1, 1)
KLINE_END = date(2026, 8, 18)


async def register_basics() -> None:
    sf = async_session_factory
    async with sf() as s:
        for code, sym, name, exch in INDEXES:
            await s.execute(
                text(
                    """
                    INSERT INTO stock_basic
                        (ts_code, symbol, name, market, exchange, list_date, is_st, is_delisted, data_source, created_at, updated_at)
                    VALUES
                        (:c, :sym, :n, '指数', :e, '2005-01-01'::date, false, false, 'manual', NOW(), NOW())
                    ON CONFLICT (ts_code) DO UPDATE SET
                        name = EXCLUDED.name,
                        market = '指数',
                        exchange = EXCLUDED.exchange,
                        updated_at = NOW()
                    """
                ),
                {"c": code, "sym": sym, "n": name, "e": exch},
            )
        await s.commit()
    print(f"[basic] 已登记/更新 {len(INDEXES)} 个指数到 stock_basic")


async def sync_klines(only: set[str] | None = None) -> None:
    sf = async_session_factory
    targets = [x for x in INDEXES if only is None or x[0] in only]
    print(f"[kline] 拉取区间 {KLINE_START} ~ {KLINE_END}，目标 {len(targets)} 个指数（含单指数重试）")
    ok = 0
    for code, *_ in targets:
        r: dict | None = None
        last_err = ""
        # 对瞬时网络失败（服务器连接失败 / Broken pipe）最多重试 3 次，指数间退避
        for attempt in range(1, 4):
            try:
                r = await sync_one_stock(sf, code, KLINE_START, KLINE_END, per_stock_timeout=300)
                if isinstance(r, dict) and r.get("success"):
                    break
                last_err = f"success={r.get('success') if isinstance(r, dict) else r} source={r.get('source') if isinstance(r, dict) else None}"
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                await asyncio.sleep(3 * attempt)
        synced = r.get("synced") if isinstance(r, dict) else None
        src = r.get("source") if isinstance(r, dict) else None
        success = isinstance(r, dict) and r.get("success")
        tag = "OK " if success else "FAIL"
        print(f"  [{tag}] {code}: synced={synced} source={src}" + ("" if success else f"  ({last_err})"))
        if success:
            ok += 1
    print(f"[kline] 成功 {ok}/{len(targets)}")


async def main(mode: str, only: set[str] | None = None) -> None:
    if mode in ("basic", "all"):
        await register_basics()
    if mode in ("kline", "all"):
        await sync_klines(only)


if __name__ == "__main__":
    _args = sys.argv[1:]
    if not _args:
        _mode = "all"
    else:
        _mode = _args[0]
    if _mode not in ("basic", "kline", "all"):
        print("usage: add_index_basics_and_klines.py [basic|kline|all] [ts_code ...]")
        raise SystemExit(2)
    # 形如 000300.SH 的参数视为"只同步这些代码"
    _only = {a for a in _args[1:] if a.endswith(".SH") or a.endswith(".SZ")} or None
    asyncio.run(main(_mode, _only))
