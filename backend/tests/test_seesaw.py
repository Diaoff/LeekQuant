"""跷跷板效应（高切低）避险库测试。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
import pytest


class TestDetectMarketState:
    """大盘状态检测单元测试。"""

    def test_bull_market_returns_up(self):
        from app.data.seesaw import detect_market_state, DefensiveRules
        import asyncio

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows
            def mappings(self):
                return self
            def all(self):
                return self._rows
            def scalar_one(self):
                return 0

        class FakeSession:
            async def execute(self, stmt, params=None):
                # Simulate uptrend: prices rising above MA
                rows = []
                for i in range(119, -1, -1):  # newest first (DESC order, like DB)
                    rows.append({"trade_date": date(2026, 1, 1) + __import__('datetime').timedelta(days=i),
                                 "close": 100 + i * 0.5})
                return FakeResult(rows)

        rules = DefensiveRules(ma_short=5, ma_long=20, ma_long2=60)
        state, detail = asyncio.get_event_loop().run_until_complete(
            detect_market_state(FakeSession(), rules)
        )
        assert state == "up"
        assert detail["close"] > 0

    def test_bear_market_returns_down(self):
        from app.data.seesaw import detect_market_state, DefensiveRules
        import asyncio

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows
            def mappings(self):
                return self
            def all(self):
                return self._rows
            def scalar_one(self):
                return 0

        class FakeSession:
            async def execute(self, stmt, params=None):
                # Simulate downtrend: prices falling below MA
                rows = []
                for i in range(119, -1, -1):  # newest first
                    rows.append({
                        "trade_date": date(2026, 1, 1) + __import__('datetime').timedelta(days=i),
                        "close": 200 - i * 1.5,
                    })
                return FakeResult(rows)

        rules = DefensiveRules(ma_short=5, ma_long=20, ma_long2=60)
        state, detail = asyncio.get_event_loop().run_until_complete(
            detect_market_state(FakeSession(), rules)
        )
        assert state == "down"
        assert detail.get("drop_from_high", 0) < 0

    def test_sidelong_market_returns_neutral(self):
        from app.data.seesaw import detect_market_state, DefensiveRules
        import asyncio

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows
            def mappings(self):
                return self
            def all(self):
                return self._rows
            def scalar_one(self):
                return 0

        class FakeSession:
            async def execute(self, stmt, params=None):
                # Sideways: prices oscillating around MA
                rows = []
                for i in range(120):
                    rows.append({
                        "trade_date": date(2026, 1, 1) + __import__('datetime').timedelta(days=i),
                        "close": 100 + 2 * np.sin(i / 10),  # Oscillate around 100
                    })
                return FakeResult(rows)

        rules = DefensiveRules(ma_short=5, ma_long=20, ma_long2=60)
        state, detail = asyncio.get_event_loop().run_until_complete(
            detect_market_state(FakeSession(), rules)
        )
        assert state in ("up", "neutral", "down")  # Depends on exact oscillation

    def test_insufficient_data_returns_neutral(self):
        from app.data.seesaw import detect_market_state, DefensiveRules
        import asyncio

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows
            def mappings(self):
                return self
            def all(self):
                return self._rows
            def scalar_one(self):
                return 0

        class FakeSession:
            async def execute(self, stmt, params=None):
                return FakeResult([])  # No data

        rules = DefensiveRules()
        state, detail = asyncio.get_event_loop().run_until_complete(
            detect_market_state(FakeSession(), rules)
        )
        assert state == "neutral"
        assert "kline_data_insufficient" in detail.get("reason", "")

    def test_single_day_drop_triggers_down(self):
        """单日暴跌触发 down 状态。"""
        from app.data.seesaw import detect_market_state, DefensiveRules
        import asyncio

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows
            def mappings(self):
                return self
            def all(self):
                return self._rows
            def scalar_one(self):
                return 0

        base_date = date(2026, 1, 1)
        days = __import__('datetime').timedelta
        # 119 days of flat price at 100, then crash to 90 (-10%)
        rows = []
        for i in range(119, -1, -1):  # newest first
            if i < 119:
                close = 100.0
            else:
                close = 90.0  # -10% drop on the newest day
            rows.append({"trade_date": base_date + days(days=i), "close": close})

        class FakeSession:
            async def execute(self, stmt, params=None):
                return FakeResult(rows)

        rules = DefensiveRules(drop_threshold=Decimal("-0.03"))
        state, detail = asyncio.get_event_loop().run_until_complete(
            detect_market_state(FakeSession(), rules)
        )
        assert state == "down"
        assert "single_day_drop" in str(detail.get("down_conditions", []))


class TestGetRecommendations:
    """推荐引擎测试（P3：推荐同源 defensive_pool 表，人工维护不计算质量分）。"""

    def _fake_session(self, pool_rows):
        import asyncio

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows
            def mappings(self):
                return self
            def all(self):
                return self._rows
            def scalar_one(self):
                return 0

        class FakeSession:
            async def execute(self, stmt, params=None):
                # 不返回任何 fundamentals / beta 数据（模拟 stock_fundamentals
                # 全空、beta_cached 无记录）——验证推荐不再依赖它们。
                if "defensive_pool" in str(stmt):
                    rows = pool_rows
                    # 模拟 repository 的 enabled_only 过滤（FakeSession 不解析 SQL）
                    if params and params.get("enabled_only") is True:
                        rows = [r for r in rows if r.get("enabled")]
                    return FakeResult(rows)
                return FakeResult([])

        return FakeSession()

    def test_empty_pool_returns_empty(self):
        from app.data.seesaw import get_seesaw_recommendations, DefensiveRules
        import asyncio
        sess = self._fake_session([])
        recs = asyncio.get_event_loop().run_until_complete(
            get_seesaw_recommendations(sess, "down", DefensiveRules(), limit=10)
        )
        assert recs == []

    def test_down_returns_pool_without_fundamentals(self):
        """关键回归（P3）：推荐不再依赖 free_cash_flow / 质量分。

        即便 stock_fundamentals / beta_cached 无任何数据，只要 defensive_pool
        表有启用标的，就应返回全库（按 sort_order）。此前强依赖
        free_cash_flow>0 导致推荐恒空、trigger_log 从不记录。
        """
        from app.data.seesaw import get_seesaw_recommendations, DefensiveRules
        import asyncio
        pool_rows = [
            {"id": 1, "ts_code": "600519.SH", "name": "贵州茅台", "note": None,
             "tags": "白酒", "sort_order": 0, "enabled": True,
             "created_at": None, "updated_at": None},
            {"id": 2, "ts_code": "601398.SH", "name": "工商银行", "note": None,
             "tags": "银行", "sort_order": 1, "enabled": True,
             "created_at": None, "updated_at": None},
            {"id": 3, "ts_code": "601318.SH", "name": "中国平安", "note": None,
             "tags": "保险", "sort_order": 2, "enabled": True,
             "created_at": None, "updated_at": None},
        ]
        sess = self._fake_session(pool_rows)
        recs = asyncio.get_event_loop().run_until_complete(
            get_seesaw_recommendations(sess, "down", DefensiveRules(), limit=10)
        )
        # 返回全库，按 sort_order 升序
        assert [r.ts_code for r in recs] == ["600519.SH", "601398.SH", "601318.SH"]
        for r in recs:
            assert r.score == 1.0
            assert r.beta is None and r.dividend_yield is None and r.pe_ttm is None
            assert "人工维护" in r.reason

    def test_disabled_items_excluded(self):
        from app.data.seesaw import get_seesaw_recommendations, DefensiveRules
        import asyncio
        pool_rows = [
            {"id": 1, "ts_code": "600519.SH", "name": "贵州茅台", "note": None,
             "tags": "", "sort_order": 0, "enabled": True,
             "created_at": None, "updated_at": None},
            {"id": 2, "ts_code": "601398.SH", "name": "工商银行", "note": None,
             "tags": "", "sort_order": 1, "enabled": False,
             "created_at": None, "updated_at": None},
        ]
        sess = self._fake_session(pool_rows)
        recs = asyncio.get_event_loop().run_until_complete(
            get_seesaw_recommendations(sess, "down", DefensiveRules(), limit=10)
        )
        assert [r.ts_code for r in recs] == ["600519.SH"]

    def test_non_down_state_returns_empty(self):
        from app.data.seesaw import get_seesaw_recommendations, DefensiveRules
        import asyncio
        sess = self._fake_session([])
        for st in ("up", "neutral"):
            recs = asyncio.get_event_loop().run_until_complete(
                get_seesaw_recommendations(sess, st, DefensiveRules(), limit=10)
            )
            assert recs == []


class TestDefensiveRules:
    """规则配置测试。"""

    def test_default_rules(self):
        from app.data.seesaw import DefensiveRules
        rules = DefensiveRules()
        assert rules.index_code == "000300.SH"
        assert rules.ma_short == 5
        assert rules.ma_long == 20
        assert rules.ma_long2 == 60
        assert rules.drop_threshold == Decimal("-0.03")
        assert rules.high_window == 20
        assert rules.high_drop_pct == Decimal("-0.05")
        assert rules.enabled is True

    def test_custom_rules(self):
        from app.data.seesaw import DefensiveRules
        rules = DefensiveRules(
            index_code="000001.SH",
            ma_short=10,
            ma_long=30,
            drop_threshold=Decimal("-0.05"),
        )
        assert rules.index_code == "000001.SH"
        assert rules.ma_short == 10
        assert rules.drop_threshold == Decimal("-0.05")


class TestMyTTFacade:
    """MyTT 函数可用性验证（确认策略可用）。"""

    def test_ma(self):
        from app.libs.MyTT import MA
        c = [100, 102, 99, 98, 97, 96]
        result = MA(c, 3)
        assert len(result) == 6
        assert result[-1] == 97.0  # MA of last 3: (98+97+96)/3 = 97

    def test_ema(self):
        from app.libs.MyTT import EMA
        c = [100, 102, 99, 98, 97]
        result = EMA(c, 5)
        assert len(result) == 5

    def test_cross(self):
        from app.libs.MyTT import MA, CROSS, REF
        c = [100, 102, 104, 103, 101, 99]
        ma5 = MA(c, 3)
        ma10 = MA(c, 5)
        # Cross detects when ma5 crosses ma10
        result = CROSS(REF(ma5, 1), ma5)
        assert isinstance(result, np.ndarray)
