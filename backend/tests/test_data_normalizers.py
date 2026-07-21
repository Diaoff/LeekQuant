from datetime import date
from decimal import Decimal

import pytest

from app.data.normalizers import normalize_daily_kline, normalize_stock_basic, normalize_trade_calendar
from app.data.validators import DataValidationError, validate_daily_kline


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
