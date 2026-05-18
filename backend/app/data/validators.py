from __future__ import annotations

from decimal import Decimal

from app.data.models import DailyKline, StockBasic, TradeCalendarDay


class DataValidationError(ValueError):
    pass


def validate_stock_basic(record: StockBasic) -> None:
    if not record.ts_code or "." not in record.ts_code:
        raise DataValidationError("stock ts_code is required")
    if not record.symbol:
        raise DataValidationError("stock symbol is required")
    if not record.name:
        raise DataValidationError("stock name is required")


def validate_trade_calendar(record: TradeCalendarDay) -> None:
    if record.cal_date is None:
        raise DataValidationError("calendar date is required")


def validate_daily_kline(record: DailyKline) -> None:
    if not record.ts_code:
        raise DataValidationError("daily kline ts_code is required")
    if record.trade_date is None:
        raise DataValidationError("daily kline trade_date is required")

    prices = {
        "open": record.open,
        "high": record.high,
        "low": record.low,
        "close": record.close,
    }
    if any(value is None for value in prices.values()):
        raise DataValidationError("daily kline OHLC fields are required")
    if any(value is not None and value < Decimal("0") for value in prices.values()):
        raise DataValidationError("daily kline OHLC fields must be non-negative")
    if record.high is not None and record.low is not None and record.high < record.low:
        raise DataValidationError("daily kline high must be greater than or equal to low")
    if record.high is not None:
        if record.open is not None and record.open > record.high:
            raise DataValidationError("daily kline open cannot be greater than high")
        if record.close is not None and record.close > record.high:
            raise DataValidationError("daily kline close cannot be greater than high")
    if record.low is not None:
        if record.open is not None and record.open < record.low:
            raise DataValidationError("daily kline open cannot be less than low")
        if record.close is not None and record.close < record.low:
            raise DataValidationError("daily kline close cannot be less than low")
    if record.volume is not None and record.volume < 0:
        raise DataValidationError("daily kline volume must be non-negative")
    if record.amount is not None and record.amount < Decimal("0"):
        raise DataValidationError("daily kline amount must be non-negative")

