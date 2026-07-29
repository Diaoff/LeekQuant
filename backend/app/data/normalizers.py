from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from app.data.models import DailyKline, StockBasic, StockFundamental, TradeCalendarDay


def parse_date(value: Any) -> date | None:
    if value is None or value == "" or pd.isna(value):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return pd.to_datetime(text).date()


def parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or pd.isna(value):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def parse_int(value: Any) -> int | None:
    if value is None or value == "" or pd.isna(value):
        return None
    return int(float(value))


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "open", "交易", "是"}


def first_value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            value = row[name]
            if value is not None and not (isinstance(value, float) and pd.isna(value)):
                return value
    return None


def infer_exchange(symbol_or_code: str) -> str:
    code = symbol_or_code.split(".")[0]
    if code.startswith(("6", "9")):
        return "SSE"
    return "SZSE"


def infer_market(symbol_or_code: str) -> str:
    code = symbol_or_code.split(".")[0]
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("30", "301")):
        return "创业板"
    if code.startswith(("4", "8")):
        return "北交所"
    return "主板"


def normalize_ts_code(value: Any) -> str:
    text = str(value).strip().upper()
    if "." in text:
        left, right = text.split(".", 1)
        if left in {"SH", "SSE"}:
            return f"{right.zfill(6)}.SH"
        if left in {"SZ", "SZSE"}:
            return f"{right.zfill(6)}.SZ"
        code, suffix = left, right
        if suffix in {"SH", "SSE"}:
            return f"{code}.SH"
        if suffix in {"SZ", "SZSE"}:
            return f"{code}.SZ"
        return text

    exchange = infer_exchange(text)
    suffix = "SH" if exchange == "SSE" else "SZ"
    return f"{text.zfill(6)}.{suffix}"


def normalize_stock_basic(row: Mapping[str, Any], source: str) -> StockBasic:
    ts_code = normalize_ts_code(first_value(row, "ts_code", "code", "stock_code", "证券代码", "代码", "公司代码"))
    symbol = ts_code.split(".", 1)[0]
    name = str(
        first_value(
            row,
            "name",
            "stock_name",
            "short_name",
            "code_name",
            "证券简称",
            "证券名称",
            "股票简称",
            "名称",
            "公司简称",
        )
        or ""
    ).strip()
    list_date = parse_date(first_value(row, "list_date", "上市日期", "ipo_date"))
    delist_date = parse_date(first_value(row, "delist_date", "退市日期", "outDate", "终止上市日期", "暂停上市日期"))
    raw_status = first_value(row, "status")
    baostock_delisted = raw_status is not None and str(raw_status).strip() == "0"
    market = first_value(row, "market", "type", "板块") or infer_market(ts_code)
    exchange = first_value(row, "exchange", "交易所") or infer_exchange(ts_code)
    # A-share exchanges rename delisted stocks to include "退" (e.g. "中弘退",
    # "退市金钰", "欧浦退"). Name-based detection is the most reliable signal
    # because data sources often omit delist_date but always carry the
    # exchange-mandated name. Mirrors how is_st detects "ST" in the name.
    name_delisted = "退" in name
    is_delisted = (
        delist_date is not None
        or baostock_delisted
        or name_delisted
        or truthy(first_value(row, "is_delisted", "退市", "delisted"))
    )

    return StockBasic(
        ts_code=ts_code,
        symbol=symbol,
        name=name,
        market=str(market).strip() if market else None,
        exchange=str(exchange).strip().upper() if exchange else None,
        industry=first_value(row, "industry", "所属行业", "行业"),
        area=first_value(row, "area", "地区"),
        list_date=list_date,
        delist_date=delist_date,
        is_st=("ST" in name.upper()) or truthy(first_value(row, "is_st", "ST")),
        is_delisted=is_delisted,
        data_source=source,
    )


def normalize_trade_calendar(row: Mapping[str, Any], source: str) -> TradeCalendarDay:
    cal_date = parse_date(first_value(row, "cal_date", "calendar_date", "trade_date", "date", "交易日", "日期"))
    if cal_date is None:
        raise ValueError("trade calendar row is missing cal_date")
    is_open_value = first_value(row, "is_open", "trade_status", "is_trading_day", "是否交易")
    if is_open_value is None:
        is_open = not cal_date.weekday() >= 5
    else:
        is_open = truthy(is_open_value) or str(is_open_value).strip() in {"1"}

    return TradeCalendarDay(
        cal_date=cal_date,
        is_open=is_open,
        pretrade_date=parse_date(first_value(row, "pretrade_date", "pretrade_day", "prev_trade_date")),
        nexttrade_date=parse_date(first_value(row, "nexttrade_date", "next_trade_date")),
        is_weekend=cal_date.weekday() >= 5,
        is_holiday=not is_open and cal_date.weekday() < 5,
        source=source,
    )


def normalize_daily_kline(
    row: Mapping[str, Any],
    source: str,
    ts_code: str | None = None,
    is_suspended: bool | None = None,
) -> DailyKline:
    row_ts_code = ts_code or first_value(row, "ts_code", "code", "股票代码", "证券代码")
    normalized_code = normalize_ts_code(row_ts_code)
    trade_date = parse_date(first_value(row, "trade_date", "date", "日期", "交易日期"))
    if trade_date is None:
        raise ValueError("daily kline row is missing trade_date")

    suspended_value = bool(is_suspended) if is_suspended is not None else False

    return DailyKline(
        ts_code=normalized_code,
        trade_date=trade_date,
        open=parse_decimal(first_value(row, "open", "开盘", "开盘价")),
        high=parse_decimal(first_value(row, "high", "最高", "最高价")),
        low=parse_decimal(first_value(row, "low", "最低", "最低价")),
        close=parse_decimal(first_value(row, "close", "收盘", "收盘价")),
        pre_close=parse_decimal(first_value(row, "pre_close", "昨收", "前收盘")),
        volume=parse_int(first_value(row, "volume", "vol", "成交量")),
        amount=parse_decimal(first_value(row, "amount", "成交额")),
        turnover_rate=parse_decimal(first_value(row, "turnover_rate", "换手率")),
        adj_factor=parse_decimal(first_value(row, "adj_factor", "复权因子")),
        is_suspended=suspended_value,
        data_source=source,
        raw_payload=dict(row),
    )


def normalize_stock_fundamental(
    row: Mapping[str, Any],
    source: str,
    ts_code: str | None = None,
    report_date: date | None = None,
) -> StockFundamental:
    """Normalize a stock fundamentals row.

    Note on ``report_date``: in this codebase ``report_date`` represents the
    *data snapshot date* (the date the fundamentals snapshot was taken),
    NOT the formal earnings announcement date. Different providers fill it
    differently:

    * ``EastMoneyHttpProvider`` / ``TencentHttpProvider`` / ``AkShareProvider``
      produce a single current snapshot and stamp it with the query
      ``end_date``.
    * ``BaostockProvider`` produces daily snapshots (each K-line row carries
      PE/PB for that trading day) and stamps each row with its own K-line
      date, preserving daily history.

    Both semantics are valid "snapshot date"; the ``latest_fund`` CTE in
    ``signal_tasks`` relies on ``ORDER BY report_date DESC`` to pick the
    freshest snapshot, which works for both shapes.
    """
    row_ts_code = ts_code or first_value(row, "ts_code", "code", "股票代码", "证券代码")
    normalized_code = normalize_ts_code(row_ts_code)
    parsed_report_date = report_date or parse_date(
        first_value(row, "report_date", "statDate", "pubDate", "date", "报告期", "公告日期", "日期")
    )
    if parsed_report_date is None:
        raise ValueError("fundamental row is missing report_date")

    return StockFundamental(
        ts_code=normalized_code,
        report_date=parsed_report_date,
        announce_date=parse_date(first_value(row, "announce_date", "pubDate", "公告日期")),
        pe_ttm=parse_decimal(first_value(row, "pe_ttm", "peTTM", "pe_ratio", "市盈率-TTM", "市盈率TTM")),
        pb=parse_decimal(first_value(row, "pb", "pbMRQ", "pb_ratio", "市净率-MRQ", "市净率")),
        ps_ttm=parse_decimal(first_value(row, "ps_ttm", "psTTM", "ps_ratio", "市销率-TTM")),
        pcf_ttm=parse_decimal(first_value(row, "pcf_ttm", "pcfNcfTTM", "pcf_ratio", "市现率-TTM")),
        roe=parse_decimal(first_value(row, "roe", "ROE", "净资产收益率")),
        roa=parse_decimal(first_value(row, "roa", "ROA", "总资产收益率")),
        market_cap=parse_decimal(first_value(row, "market_cap", "total_mv", "总市值")),
        float_market_cap=parse_decimal(first_value(row, "float_market_cap", "circ_mv", "流通市值")),
        dividend_yield=parse_decimal(first_value(row, "dividend_yield", "股息率")),
        revenue=parse_decimal(first_value(row, "revenue", "营业总收入", "营业收入")),
        net_profit=parse_decimal(first_value(row, "net_profit", "归母净利润", "净利润")),
        revenue_growth=parse_decimal(first_value(row, "revenue_growth", "YOYNI", "营收同比")),
        net_profit_growth=parse_decimal(first_value(row, "net_profit_growth", "YOYPNI", "净利润同比")),
        gross_margin=parse_decimal(first_value(row, "gross_margin", "销售毛利率")),
        debt_to_equity=parse_decimal(first_value(row, "debt_to_equity", "资产负债率")),
        current_ratio=parse_decimal(first_value(row, "current_ratio", "流动比率")),
        free_cash_flow=parse_decimal(first_value(row, "free_cash_flow", "自由现金流")),
        income_statement=dict(row) if source == "baostock_profit" else None,
        data_source=source,
    )


def dataframe_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return frame.where(pd.notna(frame), None).to_dict(orient="records")
