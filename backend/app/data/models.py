from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class StockBasic:
    ts_code: str
    symbol: str
    name: str
    market: str | None = None
    exchange: str | None = None
    industry: str | None = None
    area: str | None = None
    list_date: date | None = None
    delist_date: date | None = None
    is_st: bool = False
    is_delisted: bool = False
    data_source: str = "adata"


@dataclass(slots=True)
class TradeCalendarDay:
    cal_date: date
    is_open: bool
    pretrade_date: date | None = None
    nexttrade_date: date | None = None
    is_weekend: bool = False
    is_holiday: bool = False
    source: str = "akshare"


@dataclass(slots=True)
class DailyKline:
    ts_code: str
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    pre_close: Decimal | None = None
    volume: int | None = None
    amount: Decimal | None = None
    turnover_rate: Decimal | None = None
    adj_factor: Decimal | None = None
    is_suspended: bool = False
    is_limit_up: bool = False
    is_limit_down: bool = False
    data_source: str = "adata"
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StockFundamental:
    ts_code: str
    report_date: date
    announce_date: date | None = None
    pe_ttm: Decimal | None = None
    pb: Decimal | None = None
    ps_ttm: Decimal | None = None
    pcf_ttm: Decimal | None = None
    roe: Decimal | None = None
    roa: Decimal | None = None
    market_cap: Decimal | None = None
    float_market_cap: Decimal | None = None
    dividend_yield: Decimal | None = None
    revenue: Decimal | None = None
    net_profit: Decimal | None = None
    revenue_growth: Decimal | None = None
    net_profit_growth: Decimal | None = None
    gross_margin: Decimal | None = None
    debt_to_equity: Decimal | None = None
    current_ratio: Decimal | None = None
    free_cash_flow: Decimal | None = None
    income_statement: dict[str, Any] | None = None
    balance_sheet: dict[str, Any] | None = None
    cashflow_statement: dict[str, Any] | None = None
    data_source: str = "baostock"


@dataclass(slots=True)
class FundFlowDaily:
    ts_code: str
    trade_date: date
    main_net_amount: Decimal | None = None
    main_net_ratio: Decimal | None = None
    ultra_net_amount: Decimal | None = None
    ultra_net_ratio: Decimal | None = None
    large_net_amount: Decimal | None = None
    large_net_ratio: Decimal | None = None
    mid_net_amount: Decimal | None = None
    mid_net_ratio: Decimal | None = None
    small_net_amount: Decimal | None = None
    small_net_ratio: Decimal | None = None
    data_source: str = "akshare"
