from app.data.fetcher import DataProviderError, fetch_with_fallback
from datetime import date

from app.data.models import StockBasic, TradeCalendarDay


class EmptyProvider:
    name = "adata"

    def fetch_stock_basic(self):
        return []


class FailingProvider:
    name = "baostock"

    def fetch_stock_basic(self):
        raise DataProviderError("offline")


class SuccessProvider:
    name = "akshare"

    def fetch_stock_basic(self):
        return [StockBasic(ts_code="000001.SZ", symbol="000001", name="平安银行")]


class FailingCalendarProvider:
    name = "adata"

    def fetch_trade_calendar(self, start_date, end_date):
        raise DataProviderError("not available")


class SuccessCalendarProvider:
    name = "akshare"

    def fetch_trade_calendar(self, start_date, end_date):
        return [TradeCalendarDay(cal_date=start_date, is_open=True, source=self.name)]


def test_fetcher_uses_three_source_fallback_order() -> None:
    source, records = fetch_with_fallback(
        [EmptyProvider(), FailingProvider(), SuccessProvider()],
        "fetch_stock_basic",
    )

    assert source == "akshare"
    assert records[0].ts_code == "000001.SZ"


def test_fetcher_reports_all_provider_failures() -> None:
    try:
        fetch_with_fallback([EmptyProvider(), FailingProvider()], "fetch_stock_basic")
    except DataProviderError as exc:
        message = str(exc)
    else:
        raise AssertionError("fallback should fail when every provider fails")

    assert "adata: no records returned" in message
    assert "baostock: offline" in message


def test_fetcher_falls_back_to_akshare_trade_calendar() -> None:
    source, records = fetch_with_fallback(
        [FailingCalendarProvider(), SuccessCalendarProvider()],
        "fetch_trade_calendar",
        date(2026, 5, 18),
        date(2026, 5, 18),
    )

    assert source == "akshare"
    assert records == [TradeCalendarDay(cal_date=date(2026, 5, 18), is_open=True, source="akshare")]
