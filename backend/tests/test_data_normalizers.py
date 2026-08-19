from datetime import date
from decimal import Decimal

import pytest

from app.data.normalizers import (
    normalize_daily_kline,
    normalize_stock_basic,
    normalize_stock_fundamental,
    normalize_trade_calendar,
)
from app.data.validators import DataValidationError, validate_daily_kline


def test_normalize_stock_fundamental_maps_baostock_profit_fields() -> None:
    """Baostock query_profit_data 字段（roeAvg/gpMargin/netProfit/statDate/pubDate）映射。"""
    record = normalize_stock_fundamental(
        {
            "code": "sz.300389",
            "pubDate": "2025-04-28",
            "statDate": "2025-03-31",
            "roeAvg": "12.34",
            "npMargin": "8.5",
            "gpMargin": "30.1",
            "netProfit": "120000000.00",
            "epsTTM": "0.85",
        },
        "baostock_profit",
    )

    assert record.ts_code == "300389.SZ"
    assert record.report_date == date(2025, 3, 31)
    assert record.announce_date == date(2025, 4, 28)
    assert record.roe == Decimal("12.34")
    assert record.gross_margin == Decimal("30.1")
    assert record.net_profit == Decimal("120000000.00")
    assert record.data_source == "baostock_profit"
    assert record.income_statement is not None  # baostock_profit 钩子存整行审计


def test_normalize_stock_fundamental_maps_baostock_growth_fields() -> None:
    """Baostock query_growth_data 字段（YOYNI/YOYPNI/statDate/pubDate）映射。"""
    record = normalize_stock_fundamental(
        {
            "code": "sz.300389",
            "pubDate": "2025-04-28",
            "statDate": "2025-03-31",
            "YOYEquity": "5.1",
            "YOYAsset": "8.2",
            "YOYNI": "45.6",
            "YOYEPSBasic": "30.0",
            "YOYPNI": "42.3",
        },
        "baostock_growth",
    )

    assert record.ts_code == "300389.SZ"
    assert record.report_date == date(2025, 3, 31)
    assert record.announce_date == date(2025, 4, 28)
    assert record.revenue_growth == Decimal("45.6")
    assert record.net_profit_growth == Decimal("42.3")
    assert record.data_source == "baostock_growth"


@pytest.mark.parametrize("name_field", ["short_name", "code_name", "name", "证券简称"])
def test_normalize_stock_basic_maps_name_aliases(name_field: str) -> None:
    record = normalize_stock_basic(
        {
            "stock_code": "600000",
            name_field: "浦发银行",
            "exchange": "SSE",
            "list_date": "19991110",
            "industry": "银行",
        },
        "adata",
    )

    assert record.ts_code == "600000.SH"
    assert record.symbol == "600000"
    assert record.name == "浦发银行"
    assert record.list_date == date(1999, 11, 10)
    assert record.data_source == "adata"


def test_normalize_trade_calendar_maps_baostock_fields() -> None:
    record = normalize_trade_calendar(
        {
            "calendar_date": "2026-05-18",
            "is_trading_day": "1",
        },
        "baostock",
    )

    assert record.cal_date == date(2026, 5, 18)
    assert record.is_open is True
    assert record.is_weekend is False


def test_normalize_trade_calendar_maps_akshare_trade_date_field() -> None:
    record = normalize_trade_calendar(
        {
            "trade_date": date(2026, 5, 18),
        },
        "akshare",
    )

    assert record.cal_date == date(2026, 5, 18)
    assert record.is_open is True
    assert record.is_weekend is False


def test_normalize_stock_basic_maps_baostock_prefixed_code() -> None:
    record = normalize_stock_basic(
        {
            "code": "sh.600000",
            "code_name": "浦发银行",
        },
        "baostock",
    )

    assert record.ts_code == "600000.SH"
    assert record.symbol == "600000"
    assert record.market == "主板"


@pytest.mark.parametrize(
    ("ts_code", "expected_market"),
    [
        ("600000.SH", "主板"),
        ("300001.SZ", "创业板"),
        ("688001.SH", "科创板"),
        ("430001.BJ", "北交所"),
        ("830001.BJ", "北交所"),
    ],
)
def test_normalize_stock_basic_infers_market_from_ts_code(ts_code: str, expected_market: str) -> None:
    record = normalize_stock_basic(
        {
            "ts_code": ts_code,
            "name": "测试股票",
        },
        "adata",
    )

    assert record.market == expected_market


@pytest.mark.parametrize(
    "name",
    [
        "中弘退",      # suffix 退
        "退市金钰",    # prefix 退市
        "欧浦退",      # suffix 退
        "退市XXX",     # generic prefix pattern
        "XXX退市",     # generic suffix pattern
    ],
)
def test_normalize_stock_basic_detects_delisting_from_name(name: str) -> None:
    """A-share exchanges rename delisted stocks to include "退" — the normalizer
    must flag is_delisted=True from the name alone, even when delist_date is
    missing. Otherwise sync_stock_basic upsert overwrites is_delisted to False
    and signal generation (which filters is_delisted=FALSE) leaks BUY signals
    for delisted stocks.
    """
    record = normalize_stock_basic(
        {"ts_code": "000979.SZ", "name": name},
        "adata",
    )
    assert record.is_delisted is True


def test_normalize_stock_basic_does_not_flag_normal_name_as_delisted() -> None:
    """Sanity: stocks without "退" in name are not delisted."""
    record = normalize_stock_basic(
        {"ts_code": "600000.SH", "name": "浦发银行"},
        "adata",
    )
    assert record.is_delisted is False


def test_normalize_stock_basic_still_uses_delist_date_when_name_clean() -> None:
    """delist_date is still respected even when name doesn't contain "退"."""
    record = normalize_stock_basic(
        {"ts_code": "600000.SH", "name": "浦发银行", "delist_date": "20250101"},
        "adata",
    )
    assert record.is_delisted is True


def test_normalize_daily_kline_maps_akshare_fields() -> None:
    record = normalize_daily_kline(
        {
            "日期": "2026-05-18",
            "开盘": "10.10",
            "最高": "10.50",
            "最低": "10.00",
            "收盘": "10.30",
            "成交量": "1000",
            "成交额": "10300.5",
        },
        "akshare",
        ts_code="000001.SZ",
    )

    assert record.ts_code == "000001.SZ"
    assert record.trade_date == date(2026, 5, 18)
    assert record.open == Decimal("10.10")
    assert record.volume == 1000
    assert record.amount == Decimal("10300.5")


def test_daily_kline_validation_rejects_invalid_ohlc() -> None:
    record = normalize_daily_kline(
        {
            "date": "2026-05-18",
            "open": "11",
            "high": "10",
            "low": "9",
            "close": "9.5",
            "volume": "1",
        },
        "baostock",
        ts_code="000001.SZ",
    )

    with pytest.raises(DataValidationError, match="open cannot be greater than high"):
        validate_daily_kline(record)


def test_daily_kline_validation_rejects_negative_volume() -> None:
    record = normalize_daily_kline(
        {
            "date": "2026-05-18",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10.5",
            "volume": "-1",
        },
        "baostock",
        ts_code="000001.SZ",
    )

    with pytest.raises(DataValidationError, match="volume must be non-negative"):
        validate_daily_kline(record)


def test_daily_kline_requires_trade_date() -> None:
    with pytest.raises(ValueError, match="trade_date"):
        normalize_daily_kline(
            {
                "open": "10",
                "high": "11",
                "low": "9",
                "close": "10.5",
            },
            "adata",
            ts_code="000001.SZ",
        )


def test_normalize_daily_kline_defaults_is_suspended_to_false_when_not_provided() -> None:
    record = normalize_daily_kline(
        {
            "date": "2026-05-18",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10.5",
            "volume": "1000",
        },
        "akshare",
        ts_code="000001.SZ",
    )

    assert record.is_suspended is False


def test_normalize_daily_kline_accepts_explicit_is_suspended_true() -> None:
    record = normalize_daily_kline(
        {
            "date": "2026-05-18",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10.5",
            "volume": "1000",
        },
        "baostock",
        ts_code="000001.SZ",
        is_suspended=True,
    )

    assert record.is_suspended is True


def test_normalize_daily_kline_accepts_explicit_is_suspended_false() -> None:
    record = normalize_daily_kline(
        {
            "date": "2026-05-18",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10.5",
            "volume": "1000",
        },
        "baostock",
        ts_code="000001.SZ",
        is_suspended=False,
    )

    assert record.is_suspended is False


def test_normalize_daily_kline_none_is_suspended_falls_back_to_false() -> None:
    record = normalize_daily_kline(
        {
            "date": "2026-05-18",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10.5",
            "volume": "1000",
        },
        "baostock",
        ts_code="000001.SZ",
        is_suspended=None,
    )

    assert record.is_suspended is False
