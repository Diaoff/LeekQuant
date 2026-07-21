from datetime import date
from decimal import Decimal
from types import ModuleType
from typing import Any

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
    eastmoney = next(item for item in metadata if item["name"] == "eastmoney_http")
    assert eastmoney["capabilities"] == ["daily_kline", "fundamentals", "stock_basic"]
    assert next(item for item in metadata if item["name"] == "mootdx")["enabled"] is False


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
