from datetime import date
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest

from app.data import providers
from app.data.providers import (
    BaostockProvider,
    EastMoneyHttpProvider,
    MootdxProvider,
    ProviderCapability,
    provider_metadata,
    provider_supports,
)


def test_provider_registry_exposes_plugin_metadata() -> None:
    metadata = provider_metadata()
    names = [item["name"] for item in metadata]

    # Priority order per design: AData(1) → Baostock(2) → AkShare(3) → Mootdx(5,disabled)
    # → EastMoney HTTP(10, degraded fallback) → Tencent HTTP(20)
    assert names[:3] == ["adata", "baostock", "akshare"]
    assert "eastmoney_http" in names
    assert "tencent_http" in names
    assert "mootdx" in names
    assert "akshare_fund_flow" in names
    eastmoney = next(item for item in metadata if item["name"] == "eastmoney_http")
    assert eastmoney["capabilities"] == ["daily_kline", "fundamentals", "stock_basic"]
    assert next(item for item in metadata if item["name"] == "mootdx")["enabled"] is False
    fund_flow = next(item for item in metadata if item["name"] == "akshare_fund_flow")
    assert fund_flow["capabilities"] == ["fund_flow"]


def test_provider_supports_declared_capabilities() -> None:
    provider = EastMoneyHttpProvider()

    assert provider_supports(provider, ProviderCapability.DAILY_KLINE)
    assert not provider_supports(provider, ProviderCapability.TRADE_CALENDAR)


def test_eastmoney_stock_basic_maps_core_fields(monkeypatch) -> None:
    def fake_http_json(_url, _params=None, _timeout=15):
        return {
            "data": {
                "total": 1,
                "diff": [{"f12": "600000", "f13": 1, "f14": "浦发银行", "f100": "银行"}],
            }
        }

    monkeypatch.setattr(providers, "_http_json", fake_http_json)

    records = EastMoneyHttpProvider().fetch_stock_basic()

    assert len(records) == 1
    assert records[0].ts_code == "600000.SH"
    assert records[0].name == "浦发银行"
    assert records[0].industry == "银行"
    assert records[0].data_source == "eastmoney_http"


def test_eastmoney_daily_kline_maps_push2his_rows(monkeypatch) -> None:
    def fake_http_json(_url, params=None, _timeout=15):
        assert params["secid"] == "0.000001"
        return {
            "data": {
                "klines": [
                    "2026-05-18,10.00,10.50,10.80,9.90,100000,1050000.00,9.00,5.00,0.50,1.20"
                ]
            }
        }

    monkeypatch.setattr(providers, "_http_json", fake_http_json)

    records = EastMoneyHttpProvider().fetch_daily_kline("000001.SZ", date(2026, 5, 18), date(2026, 5, 18))

    assert len(records) == 1
    assert records[0].ts_code == "000001.SZ"
    assert records[0].trade_date == date(2026, 5, 18)
    assert records[0].close == Decimal("10.50")
    assert records[0].volume == 100000
    assert records[0].data_source == "eastmoney_http"


def test_eastmoney_fundamentals_maps_push2_snapshot(monkeypatch) -> None:
    def fake_http_json(_url, params=None, _timeout=15):
        assert params["secid"] == "1.600000"
        return {
            "data": {
                "f162": "6.50",
                "f167": "0.72",
                "f116": "120000000000",
                "f117": "119000000000",
            }
        }

    monkeypatch.setattr(providers, "_http_json", fake_http_json)

    records = EastMoneyHttpProvider().fetch_stock_fundamentals(
        ["600000.SH"],
        date(2026, 5, 1),
        date(2026, 5, 18),
    )

    assert len(records) == 1
    assert records[0].pe_ttm == Decimal("6.50")
    assert records[0].pb == Decimal("0.72")
    assert records[0].market_cap == Decimal("120000000000")
    assert records[0].data_source == "eastmoney_http"


def test_mootdx_daily_kline_maps_bars_and_converts_lots_to_shares(monkeypatch) -> None:
    import sys
    import pandas as pd

    class FakeClient:
        def bars(self, symbol, frequency, offset):
            assert symbol == "000001"
            assert frequency == 9
            assert offset >= 10
            return pd.DataFrame(
                [
                    {
                        "datetime": "2026-05-17 15:00",
                        "open": 9.9,
                        "close": 10.0,
                        "high": 10.1,
                        "low": 9.8,
                        "vol": 123.0,
                        "amount": 123000.0,
                    },
                    {
                        "datetime": "2026-05-18 15:00",
                        "open": 10.0,
                        "close": 10.5,
                        "high": 10.8,
                        "low": 9.9,
                        "vol": 747632.0,
                        "amount": 808079600.0,
                    },
                ]
            )

    class FakeQuotes:
        @staticmethod
        def factory(market):
            assert market == "std"
            return FakeClient()

    mootdx_module = ModuleType("mootdx")
    quotes_module = ModuleType("mootdx.quotes")
    quotes_module.Quotes = FakeQuotes
    monkeypatch.setitem(sys.modules, "mootdx", mootdx_module)
    monkeypatch.setitem(sys.modules, "mootdx.quotes", quotes_module)

    records = MootdxProvider().fetch_daily_kline("000001.SZ", date(2026, 5, 18), date(2026, 5, 18))

    assert len(records) == 1
    assert records[0].ts_code == "000001.SZ"
    assert records[0].trade_date == date(2026, 5, 18)
    assert records[0].close == Decimal("10.5")
    assert records[0].volume == 74763200
    assert records[0].amount == Decimal("808079600.0")
    assert records[0].data_source == "mootdx"


def _install_fake_baostock(monkeypatch, *, kline_rows: list[dict[str, str]] | None = None) -> dict[str, list[str]]:
    """Inject a fake baostock module capturing login/logout call order."""
    import sys

    call_log: list[str] = []

    class FakeLoginResult:
        error_code = "0"
        error_msg = ""

    class FakeResult:
        def __init__(self, rows: list[dict[str, str]]) -> None:
            self._rows = list(rows)
            self._index = 0
            self.error_code = "0"
            self.error_msg = ""
            self.fields = list(rows[0].keys()) if rows else []

        def next(self) -> bool:
            if self._index < len(self._rows):
                return True
            return False

        def get_row_data(self) -> list[str]:
            if self._index >= len(self._rows):
                return []
            row = self._rows[self._index]
            self._index += 1
            return [str(row.get(f, "")) for f in self.fields]

    class FakeBs:
        @staticmethod
        def login() -> FakeLoginResult:
            call_log.append("login")
            return FakeLoginResult()

        @staticmethod
        def logout() -> None:
            call_log.append("logout")

        @staticmethod
        def query_all_stock() -> FakeResult:
            return FakeResult([])

        @staticmethod
        def query_trade_dates(_start: str, _end: str) -> FakeResult:
            return FakeResult([])

        @staticmethod
        def query_history_k_data_plus(_code: str, _fields: str, **_kwargs: Any) -> FakeResult:
            return FakeResult(kline_rows or [])

    baostock_module = ModuleType("baostock")
    baostock_module.login = FakeBs.login  # type: ignore[attr-defined]
    baostock_module.logout = FakeBs.logout  # type: ignore[attr-defined]
    baostock_module.query_all_stock = FakeBs.query_all_stock  # type: ignore[attr-defined]
    baostock_module.query_trade_dates = FakeBs.query_trade_dates  # type: ignore[attr-defined]
    baostock_module.query_history_k_data_plus = FakeBs.query_history_k_data_plus  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "baostock", baostock_module)
    return {"call_log": call_log}


def test_baostock_daily_kline_marks_suspended_day_via_tradestatus(monkeypatch) -> None:
    kline_rows = [
        {
            "date": "2026-05-18",
            "code": "sh.600000",
            "open": "10.00",
            "high": "10.50",
            "low": "9.90",
            "close": "10.30",
            "preclose": "10.00",
            "volume": "100000",
            "amount": "1030000.00",
            "turn": "1.20",
            "tradestatus": "0",  # suspended
        },
        {
            "date": "2026-05-19",
            "code": "sh.600000",
            "open": "10.30",
            "high": "10.60",
            "low": "10.20",
            "close": "10.40",
            "preclose": "10.30",
            "volume": "120000",
            "amount": "1248000.00",
            "turn": "1.40",
            "tradestatus": "1",  # trading
        },
    ]
    _install_fake_baostock(monkeypatch, kline_rows=kline_rows)

    records = BaostockProvider().fetch_daily_kline("600000.SH", date(2026, 5, 18), date(2026, 5, 19))

    assert len(records) == 2
    assert records[0].trade_date == date(2026, 5, 18)
    assert records[0].is_suspended is True
    assert records[1].trade_date == date(2026, 5, 19)
    assert records[1].is_suspended is False


def test_baostock_daily_kline_defaults_is_suspended_false_when_tradestatus_missing(monkeypatch) -> None:
    kline_rows = [
        {
            "date": "2026-05-18",
            "code": "sh.600000",
            "open": "10.00",
            "high": "10.50",
            "low": "9.90",
            "close": "10.30",
            "preclose": "10.00",
            "volume": "100000",
            "amount": "1030000.00",
            "turn": "1.20",
            # tradestatus intentionally missing
        },
    ]
    _install_fake_baostock(monkeypatch, kline_rows=kline_rows)

    records = BaostockProvider().fetch_daily_kline("600000.SH", date(2026, 5, 18), date(2026, 5, 18))

    assert len(records) == 1
    assert records[0].is_suspended is False


def test_baostock_run_serializes_login_logout_across_threads(monkeypatch) -> None:
    """Concurrent _run calls must not interleave login/logout.

    Verifies baostock's global login state is protected by an instance lock:
    each call should observe [login, ..., logout] in strict order with no
    nesting (no login before previous logout completes).
    """
    import threading
    import time

    state = _install_fake_baostock(monkeypatch)
    call_log: list[str] = state["call_log"]
    lock = threading.Lock()
    # Simulate bs.login/logout taking a small amount of time to surface races.
    original_login = list(call_log)

    def slow_login() -> Any:
        time.sleep(0.01)
        with lock:
            call_log.append("login-start")
        time.sleep(0.02)
        with lock:
            call_log.append("login-end")
        result = type("R", (), {"error_code": "0", "error_msg": ""})()
        return result

    def slow_logout() -> None:
        with lock:
            call_log.append("logout-start")
        time.sleep(0.02)
        with lock:
            call_log.append("logout-end")

    import sys

    bs_module = sys.modules["baostock"]
    bs_module.login = slow_login  # type: ignore[attr-defined]
    bs_module.logout = slow_logout  # type: ignore[attr-defined]

    provider = BaostockProvider()

    def worker() -> str:
        result = provider._run(lambda bs: "ok")
        return result

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 5 calls × 4 events each = 20 events; ensure no interleaving
    assert len(call_log) == 20, f"expected 20 events, got {len(call_log)}: {call_log}"

    # Each call should produce login-start, login-end, logout-start, logout-end
    # consecutively (no nesting between calls).
    for i in range(5):
        chunk = call_log[i * 4 : (i + 1) * 4]
        assert chunk == ["login-start", "login-end", "logout-start", "logout-end"], (
            f"call {i} interleaved: {chunk} (full log: {call_log})"
        )


def _install_fake_baostock_quarterly(monkeypatch, profit_rows=None, growth_rows=None):
    """注入带 query_profit_data / query_growth_data 的 fake baostock，记录调用序列。"""
    import sys

    call_log: list[str] = []

    class FakeLoginResult:
        error_code = "0"
        error_msg = ""

    class FakeResult:
        def __init__(self, rows):
            self._rows = list(rows)
            self._index = 0
            self.error_code = "0"
            self.error_msg = ""
            self.fields = list(rows[0].keys()) if rows else []

        def next(self) -> bool:
            if self._index < len(self._rows):
                return True
            return False

        def get_row_data(self):
            if self._index >= len(self._rows):
                return []
            row = self._rows[self._index]
            self._index += 1
            return [str(row.get(f, "")) for f in self.fields]

    class FakeBs:
        @staticmethod
        def login():
            call_log.append("login")
            return FakeLoginResult()

        @staticmethod
        def logout():
            call_log.append("logout")

        @staticmethod
        def query_profit_data(code=None, year=None, quarter=None):
            call_log.append(f"profit:{year}Q{quarter}")
            # 真实 Baostock 每季度只返回当期行；fake 仅 2025Q1 返回
            if (year, quarter) == (2025, 1):
                return FakeResult(profit_rows or [])
            return FakeResult([])

        @staticmethod
        def query_growth_data(code=None, year=None, quarter=None):
            call_log.append(f"growth:{year}Q{quarter}")
            if (year, quarter) == (2025, 1):
                return FakeResult(growth_rows or [])
            return FakeResult([])

    baostock_module = ModuleType("baostock")
    baostock_module.login = FakeBs.login  # type: ignore[attr-defined]
    baostock_module.logout = FakeBs.logout  # type: ignore[attr-defined]
    baostock_module.query_profit_data = FakeBs.query_profit_data  # type: ignore[attr-defined]
    baostock_module.query_growth_data = FakeBs.query_growth_data  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "baostock", baostock_module)
    return {"call_log": call_log}


def test_baostock_fetch_profit_data_maps_roe_and_dates(monkeypatch) -> None:
    state = _install_fake_baostock_quarterly(
        monkeypatch,
        profit_rows=[{
            "code": "sz.300389",
            "pubDate": "2025-04-28",
            "statDate": "2025-03-31",
            "roeAvg": "12.34",
            "gpMargin": "30.10",
            "netProfit": "120000000.00",
        }],
    )
    records = BaostockProvider().fetch_profit_data(
        ["300389.SZ"], date(2025, 1, 1), date(2025, 12, 31)
    )
    assert len(records) == 1
    r = records[0]
    assert r.ts_code == "300389.SZ"
    assert r.report_date == date(2025, 3, 31)
    assert r.announce_date == date(2025, 4, 28)
    assert r.roe == Decimal("12.34")
    assert r.gross_margin == Decimal("30.10")
    assert r.net_profit == Decimal("120000000.00")
    assert r.data_source == "baostock_profit"
    # 逐季度调用：start 2025-01-01 → year 2024..2025，共 8 个季度
    assert sum(1 for c in state["call_log"] if c.startswith("profit:")) == 8
    assert state["call_log"][0] == "login"
    assert state["call_log"][-1] == "logout"


def test_baostock_fetch_growth_data_maps_yoy_fields(monkeypatch) -> None:
    _install_fake_baostock_quarterly(
        monkeypatch,
        growth_rows=[{
            "code": "sz.300389",
            "pubDate": "2025-04-28",
            "statDate": "2025-03-31",
            "YOYNI": "45.6",
            "YOYPNI": "42.3",
        }],
    )
    records = BaostockProvider().fetch_growth_data(
        ["300389.SZ"], date(2025, 1, 1), date(2025, 12, 31)
    )
    assert len(records) == 1
    r = records[0]
    assert r.revenue_growth == Decimal("45.6")
    assert r.net_profit_growth == Decimal("42.3")
    assert r.data_source == "baostock_growth"


def test_parse_ohlc_adaptive_stock_format() -> None:
    """个股 qfqday 格式 [日期,开,收,高,低,量]。"""
    from app.data.providers import _parse_ohlc_adaptive
    # open=10.0 close=10.5 high=11.0 low=9.5
    ohlc = _parse_ohlc_adaptive(["2026-08-12", "10.0", "10.5", "11.0", "9.5", "1000"])
    assert ohlc == (10.0, 10.5, 11.0, 9.5)


def test_parse_ohlc_adaptive_index_format() -> None:
    """指数 day 格式 [日期,开,高,低,收,量]。"""
    from app.data.providers import _parse_ohlc_adaptive
    # open=3000 close=3050 high=3100 low=2950
    ohlc = _parse_ohlc_adaptive(["2026-08-12", "3000", "3100", "2950", "3050", "1000"])
    assert ohlc == (3000.0, 3050.0, 3100.0, 2950.0)


def test_parse_ohlc_adaptive_rejects_invalid() -> None:
    """全 0 / 负值 / 列序全不合法 → None（停牌日等异常行跳过）。"""
    from app.data.providers import _parse_ohlc_adaptive
    assert _parse_ohlc_adaptive(["2026-08-12", "0", "0", "0", "0", "0"]) is None
    assert _parse_ohlc_adaptive(["2026-08-12", "-1", "2", "3", "4", "5"]) is None
    assert _parse_ohlc_adaptive(["2026-08-12", "abc", "1", "2", "3", "4"]) is None


def test_akshare_fund_flow_provider_registered() -> None:
    from app.data.providers import AkShareFundFlowProvider, provider_metadata
    metadata = provider_metadata()
    ff = next(item for item in metadata if item["name"] == "akshare_fund_flow")
    assert ff["display_name"] == "AkShare Fund Flow"
    assert ff["capabilities"] == ["fund_flow"]


def test_akshare_fund_flow_provider_unsupported_methods(monkeypatch) -> None:
    from app.data.providers import AkShareFundFlowProvider, ProviderCapability
    provider = AkShareFundFlowProvider()
    from app.data.providers import DataProviderError
    with pytest.raises(DataProviderError):
        provider.fetch_stock_basic()
    with pytest.raises(DataProviderError):
        provider.fetch_trade_calendar(date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(DataProviderError):
        provider.fetch_daily_kline("600519.SH", date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(DataProviderError):
        provider.fetch_stock_fundamentals(["600519.SH"], date(2026, 1, 1), date(2026, 1, 31))


def test_akshare_fund_flow_fetch_maps_fields(monkeypatch) -> None:
    import sys
    import pandas as pd
    from app.data.providers import AkShareFundFlowProvider

    fake_df = pd.DataFrame({
        "日期": ["2026-08-18", "2026-08-19"],
        "主力净流入-净额": [-941517040, 82581703],
        "主力净流入-净占比": [-19.39, 5.17],
        "超大单净流入-净额": [-627993728, 15049079],
        "超大单净流入-净占比": [-12.93, 0.94],
        "大单净流入-净额": [-313523312, 67532624],
        "大单净流入-净占比": [-6.46, 3.95],
        "中单净流入-净额": [941730816, -53285152],
        "中单净流入-净占比": [19.40, -3.11],
        "小单净流入-净额": [-213785, -29296551],
        "小单净流入-净占比": [-0.00, -1.71],
    })

    class FakeAk:
        @staticmethod
        def stock_individual_fund_flow(stock, market):
            assert stock == "600519"
            assert market == "sh"
            return fake_df

    monkeypatch.setitem(sys.modules, "akshare", FakeAk)

    records = AkShareFundFlowProvider().fetch_fund_flow(
        ["600519.SH"],
        date(2026, 8, 1),
        date(2026, 8, 31),
    )

    assert len(records) == 2
    assert records[0].ts_code == "600519.SH"
    assert records[0].trade_date == date(2026, 8, 18)
    assert records[0].main_net_amount == Decimal("-941517040")
    assert records[0].main_net_ratio == Decimal("-19.39")
    assert records[0].ultra_net_amount == Decimal("-627993728")
    assert records[0].large_net_amount == Decimal("-313523312")
    assert records[1].trade_date == date(2026, 8, 19)
    assert records[1].main_net_amount == Decimal("82581703")
    assert records[1].data_source == "akshare"


def test_akshare_fund_flow_filters_by_date_range(monkeypatch) -> None:
    import sys
    import pandas as pd
    from app.data.providers import AkShareFundFlowProvider

    fake_df = pd.DataFrame({
        "日期": ["2026-07-01", "2026-08-15", "2026-09-01"],
        "主力净流入-净额": [100, 200, 300],
        "主力净流入-净占比": [1.0, 2.0, 3.0],
        "超大单净流入-净额": [10, 20, 30],
        "超大单净流入-净占比": [0.1, 0.2, 0.3],
        "大单净流入-净额": [90, 180, 270],
        "大单净流入-净占比": [0.9, 1.8, 2.7],
        "中单净流入-净额": [-50, -100, -150],
        "中单净流入-净占比": [-0.5, -1.0, -1.5],
        "小单净流入-净额": [-50, -100, -150],
        "小单净流入-净占比": [-0.5, -1.0, -1.5],
    })

    class FakeAk:
        @staticmethod
        def stock_individual_fund_flow(stock, market):
            return fake_df

    monkeypatch.setitem(sys.modules, "akshare", FakeAk)

    records = AkShareFundFlowProvider().fetch_fund_flow(
        ["600519.SH"],
        date(2026, 8, 1),
        date(2026, 8, 31),
    )

    assert len(records) == 1
    assert records[0].trade_date == date(2026, 8, 15)


def test_provider_supports_fund_flow_capability() -> None:
    from app.data.providers import AkShareFundFlowProvider, provider_supports
    provider = AkShareFundFlowProvider()
    assert provider_supports(provider, ProviderCapability.FUND_FLOW)
    assert not provider_supports(provider, ProviderCapability.DAILY_KLINE)
