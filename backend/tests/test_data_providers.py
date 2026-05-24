from datetime import date
from decimal import Decimal
from types import ModuleType

from app.data import providers
from app.data.providers import EastMoneyHttpProvider, MootdxProvider, ProviderCapability, provider_metadata, provider_supports


def test_provider_registry_exposes_plugin_metadata() -> None:
    metadata = provider_metadata()
    names = [item["name"] for item in metadata]

    assert names[:3] == ["eastmoney_http", "tencent_http", "mootdx"]
    assert "adata" in names
    assert metadata[0]["capabilities"] == ["daily_kline", "fundamentals", "stock_basic"]
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
