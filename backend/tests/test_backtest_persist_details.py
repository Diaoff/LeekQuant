"""Regression tests for backtest detail persistence (子表写入).

历史 bug：adapter 把 trade_date/entry_date/exit_date 序列化成字符串，
而子表日期列为 DATE，asyncpg 不接受裸字符串，直接传入会抛
AttributeError: 'str'（见回测 #149 写入失败），导致子表永远为空、
盈亏排行/交易明细无数据。_as_date 负责把字符串归一化为 date 对象。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.backtest import tasks as bt_tasks


class FakeSession:
    def __init__(self):
        self.params_log: list = []

    async def execute(self, statement, params=None):
        # params 可能是单 dict 或 dict 列表；统一收集便于断言
        if isinstance(params, list):
            self.params_log.extend(params)
        else:
            self.params_log.append(params or {})


def _sample_results() -> dict:
    return {
        "trade_records": [
            {
                "ts_code": "600000.SH",
                "trade_date": "2026-01-05",  # str, 由 adapter 序列化
                "direction": "买入",
                "price": 10.5,
                "volume": 100,
                "amount": 1050.0,
            }
        ],
        "closed_lots": [
            {
                "ts_code": "600000.SH",
                "shares": 100,
                "entry_price": 10.0,
                "entry_date": "2026-01-05",
                "exit_price": 11.0,
                "exit_date": "2026-02-01",
                "entry_fee": 1.0,
                "exit_fee": 1.0,
                "gross_pnl": 100.0,
                "net_pnl": 98.0,
                "return_rate": 0.098,
                "holding_days": 27,
                "exit_reason": "止盈",
            }
        ],
        "stock_rankings": [
            {
                "ts_code": "600000.SH",
                "closed_lot_count": 1,
                "net_pnl": 98.0,
                "win_rate": 1.0,
                "return_rate": 0.098,
                "max_profit": 98.0,
                "max_loss": 0.0,
            }
        ],
    }


def test_as_date_normalizes_strings_and_objects() -> None:
    assert bt_tasks._as_date("2026-01-05") == date(2026, 1, 5)
    assert bt_tasks._as_date("20260105") == date(2026, 1, 5)
    assert bt_tasks._as_date(date(2026, 3, 4)) == date(2026, 3, 4)
    assert bt_tasks._as_date(None) is None
    assert bt_tasks._as_date("garbage") is None


@pytest.mark.asyncio
async def test_persist_backtest_details_writes_dates_as_date_objects() -> None:
    session = FakeSession()
    await bt_tasks._persist_backtest_details(session, 9999, _sample_results())

    # 至少应有 trade / lot / ranking 三类 INSERT 的参数
    trade = next(p for p in session.params_log if p.get("trade_date") is not None)
    lot = next(
        p
        for p in session.params_log
        if p.get("entry_date") is not None and p.get("exit_date") is not None
    )
    assert isinstance(trade["trade_date"], date)
    assert isinstance(lot["entry_date"], date)
    assert isinstance(lot["exit_date"], date)
    assert trade["trade_date"] == date(2026, 1, 5)
    assert lot["entry_date"] == date(2026, 1, 5)
    assert lot["exit_date"] == date(2026, 2, 1)
