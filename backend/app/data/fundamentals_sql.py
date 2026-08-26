"""Field-wise latest-non-null SQL helpers for ``stock_fundamentals``.

The ``stock_fundamentals`` table intentionally mixes two row shapes:

* **daily valuation snapshots** — ``report_date`` = the query date, with
  ``pe_ttm``/``pb``/``ps_ttm``/``pcf_ttm``/``market_cap`` populated and the
  financial-report-derived columns (``roe``/``gross_margin``/...) left NULL.
* **periodic financial reports** — ``report_date`` = the period end, with the
  derived fundamentals populated and the valuation columns left NULL.

A naive ``DISTINCT ON (ts_code) ORDER BY report_date DESC`` (the historical
``latest_fundamentals`` CTE) returns the *whole* latest row per stock, which
**silently NULLs out the financial-report fields** whenever a newer daily
valuation snapshot exists. The helpers below instead emit a correlated
subquery that returns the latest *non-null* value per column, so both shapes
coexist. They rely on the existing
``idx_fundamentals_code_date (ts_code, report_date DESC)`` index.
"""
from __future__ import annotations


def latest_fundamental_field(column: str, outer_alias: str = "s") -> str:
    """Correlated subquery returning the latest non-null ``column`` for the stock.

    ``outer_alias`` is the alias of the outer ``stock_basic`` row the subquery
    is correlated to (``s`` in ``stock_service``, ``sb`` in ``signal_tasks``).
    """
    return (
        f"(SELECT {column} FROM stock_fundamentals sf "
        f"WHERE sf.ts_code = {outer_alias}.ts_code AND sf.{column} IS NOT NULL "
        f"ORDER BY sf.report_date DESC LIMIT 1)"
    )


def latest_fundamental_report_date(outer_alias: str = "s") -> str:
    """Most recent snapshot date across all fundamentals for the stock (display only)."""
    return (
        f"(SELECT MAX(report_date) FROM stock_fundamentals sf "
        f"WHERE sf.ts_code = {outer_alias}.ts_code)"
    )


def latest_fundamental_source(outer_alias: str = "s") -> str:
    """``data_source`` of the most recent snapshot row for the stock."""
    return (
        f"(SELECT data_source FROM stock_fundamentals sf "
        f"WHERE sf.ts_code = {outer_alias}.ts_code "
        f"ORDER BY sf.report_date DESC LIMIT 1)"
    )


# Columns surfaced by the stock listing / detail queries. The valuation block
# comes from daily snapshots; the derived-fundamentals block is what the
# 同花顺 financial-report backfill populates (roe / gross_margin / growth / ...).
_FUNDAMENTAL_FIELDS = (
    "pe_ttm",
    "pb",
    "ps_ttm",
    "pcf_ttm",
    "market_cap",
    "float_market_cap",
    "roe",
    "roa",
    "revenue_growth",
    "net_profit_growth",
    "gross_margin",
    "debt_to_equity",
    "free_cash_flow",
)


def fundamental_select_fragment(outer_alias: str = "s") -> str:
    """Comma-joined ``<subquery> AS <column>`` expressions for all surfaced fields."""
    parts = [
        latest_fundamental_field(col, outer_alias) + f" AS {col}"
        for col in _FUNDAMENTAL_FIELDS
    ]
    parts.append(latest_fundamental_report_date(outer_alias) + " AS report_date")
    parts.append(latest_fundamental_source(outer_alias) + " AS fundamentals_source")
    return ",\n                    ".join(parts)
