from __future__ import annotations

from app.data.models import StockBasic


EXCLUDED_MARKETS = {"科创板", "北交所", "京交所"}
EXCLUDED_EXCHANGES = {"BJ", "BSE"}
EXCLUDED_PREFIXES = (
    "688",
    "689",
    "4",
    "8",
    "920",
    "200",
    "900",
)

def excluded_stock_sql_condition(alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    return f"""
(
    (
        COALESCE({prefix}market, '') IN ('科创板', '北交所', '京交所')
        OR COALESCE({prefix}exchange, '') IN ('BJ', 'BSE')
        OR split_part({prefix}ts_code, '.', 1) LIKE '688%'
        OR split_part({prefix}ts_code, '.', 1) LIKE '689%'
        OR split_part({prefix}ts_code, '.', 1) LIKE '4%'
        OR split_part({prefix}ts_code, '.', 1) LIKE '8%'
        OR split_part({prefix}ts_code, '.', 1) LIKE '920%'
        OR split_part({prefix}ts_code, '.', 1) LIKE '200%'
        OR split_part({prefix}ts_code, '.', 1) LIKE '900%'
    )
    AND COALESCE({prefix}market, '') <> '指数'
)
"""


def supported_stock_sql_condition(alias: str | None = None) -> str:
    return "NOT " + excluded_stock_sql_condition(alias)


SUPPORTED_STOCK_SQL_CONDITION = supported_stock_sql_condition()
EXCLUDED_STOCK_SQL_CONDITION = excluded_stock_sql_condition()


def is_excluded_stock_code(ts_code: str) -> bool:
    code = ts_code.split(".", 1)[0].strip()
    return code.startswith(EXCLUDED_PREFIXES)


def is_supported_stock_basic(record: StockBasic) -> bool:
    market = (record.market or "").strip()
    exchange = (record.exchange or "").strip().upper()
    return (
        market not in EXCLUDED_MARKETS
        and exchange not in EXCLUDED_EXCHANGES
        and not is_excluded_stock_code(record.ts_code)
    )
