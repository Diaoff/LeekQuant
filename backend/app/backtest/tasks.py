"""Celery tasks for backtest execution."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar
from app.backtest.kline_cache import get_cached_klines, set_cached_klines
from app.backtest.cost import FeeConfig, build_fee_config
from app.db.session import async_session_factory, engine as db_engine
from app.preferences.service import get_trading_fee_config
from app.core.asyncio_runtime import run_async
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

RISK_CONFIG_FIELDS = (
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_stop_pct",
    "trailing_activation_pct",
    "time_stop_days",
)

MARKET_TARGETS = {"主板", "创业板", "科创板", "北交所"}
MARKET_TARGET_ORDER = ("主板", "创业板", "科创板", "北交所")


async def _fetch_benchmark_klines(session: AsyncSession, benchmark_code: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT trade_date, close
            FROM daily_kline
            WHERE ts_code = :code
              AND trade_date BETWEEN :start AND :end
              AND close IS NOT NULL
            ORDER BY trade_date
            """
        ),
        {"code": benchmark_code, "start": start_date, "end": end_date},
    )
    return [dict(row) for row in result.mappings().all()]


async def _fetch_klines_for_codes(
    session: AsyncSession,
    codes: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch raw daily_kline rows for arbitrary codes (benchmark / extra series).

    Reuses the exact same column set as the main stock-pool load so the
    resulting KBar objects are field-compatible. Returns code -> list of row
    dicts; codes with no data are simply absent from the result.
    """
    if not codes:
        return {}
    result = await session.execute(
        text(
            """
            SELECT ts_code, trade_date, open, high, low, close, pre_close,
                   volume, amount, turnover_rate, adj_factor, is_suspended,
                   is_limit_up, is_limit_down
            FROM daily_kline
            WHERE ts_code = ANY(CAST(:codes AS VARCHAR[]))
              AND trade_date BETWEEN :start_date AND :end_date
            ORDER BY ts_code, trade_date
            """
        ),
        {"codes": list(codes), "start_date": start_date, "end_date": end_date},
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        rd = dict(row)
        out.setdefault(rd["ts_code"], []).append(rd)
    return out


def _compute_benchmark_metrics(
    strategy_values: list[float],
    strategy_dates: list[str],
    benchmark_rows: list[dict[str, Any]],
    benchmark_code: str = "",
) -> dict[str, Any]:
    if not benchmark_rows or len(strategy_values) < 2:
        return {}

    bm_dates = {str(r["trade_date"]): float(r["close"]) for r in benchmark_rows}
    bm_values = [bm_dates.get(d) for d in strategy_dates]
    bm_values = [v for v in bm_values if v is not None]

    if len(bm_values) < 2 or bm_values[0] <= 0:
        return {}

    bm_returns = [(bm_values[i] - bm_values[i - 1]) / bm_values[i - 1] for i in range(1, len(bm_values))]
    bm_total_return = (bm_values[-1] / bm_values[0]) - 1

    from datetime import date as date_cls
    d0 = date_cls.fromisoformat(strategy_dates[0])
    d1 = date_cls.fromisoformat(strategy_dates[-1])
    years = max((d1 - d0).days / 365.25, 0.01)
    bm_annual_return = ((1 + bm_total_return) ** (1 / years)) - 1

    strategy_total = (strategy_values[-1] / strategy_values[0]) - 1 if strategy_values[0] > 0 else 0
    strategy_annual = ((1 + strategy_total) ** (1 / years)) - 1
    alpha = strategy_annual - bm_annual_return

    import statistics
    min_len = min(len(strategy_values), len(bm_values))
    s_returns = [(strategy_values[i] - strategy_values[i - 1]) / strategy_values[i - 1] for i in range(1, min_len)]
    b_returns = bm_returns[:min_len - 1] if len(bm_returns) >= min_len - 1 else bm_returns

    if len(s_returns) == len(b_returns) and len(s_returns) > 1:
        excess = [s - b for s, b in zip(s_returns, b_returns, strict=False)]
        tracking_error = statistics.stdev(excess) * (252 ** 0.5)
        information_ratio = (strategy_annual - bm_annual_return) / tracking_error if tracking_error > 0 else 0
    else:
        tracking_error = 0
        information_ratio = 0

    return {
        "benchmark_code": benchmark_code,
        "benchmark_total_return": round(bm_total_return, 8),
        "benchmark_annual_return": round(bm_annual_return, 8),
        "alpha": round(alpha, 8),
        "tracking_error": round(tracking_error, 8),
        "information_ratio": round(information_ratio, 4),
        "benchmark_curve": [{"date": d, "value": v} for d, v in zip(strategy_dates, bm_values, strict=False)],
    }


def _decode_json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            logger.debug("silent except in _decode_json_dict")
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _merge_backtest_config(strategy_config_value: Any, params_snapshot_value: Any) -> dict[str, Any]:
    """Merge persistent strategy config with per-run backtest config.

    Backtest submission stores per-run options in params_snapshot.config. Those
    options must override strategy defaults because they come from the run modal.
    """
    strategy_config = _decode_json_dict(strategy_config_value)
    params_snapshot = _decode_json_dict(params_snapshot_value)
    runtime_config = _decode_json_dict(params_snapshot.get("config"))

    merged = dict(strategy_config)
    for key, value in runtime_config.items():
        if key not in RISK_CONFIG_FIELDS and key not in {"risk_config", "fee_config"}:
            merged[key] = value

    fee_config = _decode_json_dict(strategy_config.get("fee_config"))
    fee_config.update(_decode_json_dict(runtime_config.get("fee_config")))
    if fee_config:
        merged["fee_config"] = fee_config

    risk_config = _decode_json_dict(strategy_config.get("risk_config"))
    risk_config.update(_decode_json_dict(runtime_config.get("risk_config")))
    for key in RISK_CONFIG_FIELDS:
        value = runtime_config.get(key)
        if value not in (None, ""):
            risk_config[key] = value
    if risk_config:
        merged["risk_config"] = risk_config

    return merged


def _fee_config_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, FeeConfig):
        return {
            "commission_rate": value.commission_rate,
            "min_commission": value.min_commission,
            "stamp_tax_rate": value.stamp_tax_rate,
            "transfer_fee_rate": value.transfer_fee_rate,
            "waive_min_commission": value.waive_min_commission,
        }
    return _decode_json_dict(value)


def _merge_fee_config(global_config: FeeConfig | None, local_config: Any) -> FeeConfig:
    global_dict = _fee_config_dict(global_config)
    local_dict = _fee_config_dict(local_config)
    return build_fee_config(global_dict, local_dict)


def _has_risk_controls(risk_config: dict[str, Any]) -> bool:
    for key in RISK_CONFIG_FIELDS:
        value = risk_config.get(key)
        if value in (None, ""):
            continue
        try:
            if float(value) > 0:
                return True
        except (TypeError, ValueError):
            logger.debug("silent except in _has_risk_controls")
            continue
    return False


def _stock_scope_diagnostics(stock_codes: list[str]) -> dict[str, Any]:
    return {
        "stock_count": len(stock_codes),
    }


def _normalize_market_targets(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    selected = {
        str(item).strip()
        for item in raw_values
        if item is not None and str(item).strip()
    }
    return [market for market in MARKET_TARGET_ORDER if market in selected]


def _target_from_snapshot(params_snapshot_value: Any) -> dict[str, Any]:
    params_snapshot = _decode_json_dict(params_snapshot_value)
    target = _decode_json_dict(params_snapshot.get("target"))
    target_type = target.get("type") or params_snapshot.get("target_type") or "all"
    target_value = target.get("value") or params_snapshot.get("target_value")
    if target_type == "market":
        target_value = _normalize_market_targets(target_value)
        if not target_value:
            target_type = "all"
            target_value = None
    if target_type == "watchlist_group" and not target_value:
        target_type = "all"
    return {"type": target_type, "value": target_value}


def _filters_from_snapshot(params_snapshot_value: Any, target: dict[str, Any] | None = None) -> dict[str, bool]:
    params_snapshot = _decode_json_dict(params_snapshot_value)
    filters = _decode_json_dict(params_snapshot.get("filters"))
    target_type = (target or _target_from_snapshot(params_snapshot)).get("type", "all")
    default_filter = target_type in {"all", "market"}
    return {
        "exclude_st": bool(filters.get("exclude_st", default_filter)),
        "exclude_loss_pe": bool(filters.get("exclude_loss_pe", default_filter)),
    }


def _stock_filter_clauses(filters: dict[str, bool]) -> tuple[list[str], str]:
    clauses = ["s.is_delisted = FALSE"]
    fundamentals_join = ""
    if filters.get("exclude_st"):
        clauses.append("s.is_st = FALSE")
    if filters.get("exclude_loss_pe"):
        fundamentals_join = """
            LEFT JOIN LATERAL (
                SELECT pe_ttm
                FROM stock_fundamentals sf
                WHERE sf.ts_code = s.ts_code
                  AND sf.report_date <= :start_date
                ORDER BY sf.report_date DESC
                LIMIT 1
            ) f ON TRUE
        """
        clauses.append("(f.pe_ttm IS NULL OR f.pe_ttm > 0)")
    return clauses, fundamentals_join


async def _resolve_stock_codes(
    session: AsyncSession,
    user_id: int,
    target: dict[str, Any],
    start_date: date,
    filters: dict[str, bool],
) -> list[str]:
    target_type = target["type"]
    target_value = target["value"]
    filter_clauses, fundamentals_join = _stock_filter_clauses(filters)
    where_sql = " AND ".join(filter_clauses)

    if target_type == "market":
        markets = _normalize_market_targets(target_value)
        market_placeholders = []
        params: dict[str, Any] = {"start_date": start_date}
        for index, market in enumerate(markets):
            key = f"market_{index}"
            market_placeholders.append(f":{key}")
            params[key] = market
        result = await session.execute(
            text(
                f"""
                SELECT s.ts_code
                FROM stock_basic s
                {fundamentals_join}
                WHERE s.market IN ({', '.join(market_placeholders)})
                  AND {where_sql}
                ORDER BY symbol
                """
            ),
            params,
        )
        return [r["ts_code"] for r in result.mappings().all()]

    if target_type == "watchlist_group":
        result = await session.execute(
            text(
                f"""
                SELECT w.ts_code
                FROM watchlist w
                JOIN stock_basic s ON s.ts_code = w.ts_code
                {fundamentals_join}
                WHERE w.user_id = :user_id
                  AND w.group_name = :group_name
                  AND {where_sql}
                ORDER BY w.sort_order, w.added_at
                """
            ),
            {"user_id": user_id, "group_name": target_value, "start_date": start_date},
        )
        return [r["ts_code"] for r in result.mappings().all()]

    result = await session.execute(
        text(
            f"""
            SELECT s.ts_code
            FROM stock_basic s
            {fundamentals_join}
            WHERE {where_sql}
            ORDER BY symbol
            """
        ),
        {"start_date": start_date},
    )
    return [r["ts_code"] for r in result.mappings().all()]


def _parse_kline_rows(rows: list[dict[str, Any]]) -> list[KBar]:
    return [
        KBar(
            ts_code=r["ts_code"],
            trade_date=r["trade_date"] if isinstance(r["trade_date"], date) else date.fromisoformat(str(r["trade_date"])),
            open=Decimal(str(r["open"])) if r["open"] is not None else Decimal("0"),
            high=Decimal(str(r["high"])) if r["high"] is not None else Decimal("0"),
            low=Decimal(str(r["low"])) if r["low"] is not None else Decimal("0"),
            close=Decimal(str(r["close"])) if r["close"] is not None else Decimal("0"),
            pre_close=Decimal(str(r["pre_close"])) if r.get("pre_close") is not None else Decimal("0"),
            volume=r["volume"] or 0,
            amount=Decimal(str(r["amount"])) if r.get("amount") is not None else Decimal("0"),
            turnover_rate=Decimal(str(r["turnover_rate"])) if r.get("turnover_rate") is not None else None,
            adj_factor=Decimal(str(r["adj_factor"])) if r.get("adj_factor") is not None else None,
            is_suspended=bool(r.get("is_suspended", False)),
            is_limit_up=bool(r.get("is_limit_up", False)),
            is_limit_down=bool(r.get("is_limit_down", False)),
        )
        for r in rows
    ]


@celery_app.task(name="app.tasks.run_backtest", bind=True, max_retries=1)
def run_backtest_task(self, backtest_id: int) -> dict[str, Any]:
    """Execute a backtest by running user strategy code against historical K-line data."""

    async def _run() -> dict[str, Any]:
        # ---- Phase 1: load config + fetch all data (short-lived session) ----
        # IMPORTANT: this session is closed BEFORE the long pure-Python backtest
        # run. Previously the SAME connection (with an open transaction) was held
        # for the whole ~14-minute compute; Postgres' idle_in_transaction_session_timeout
        # (30s) then killed the idle-in-transaction connection, and the failure only
        # surfaced at the final write as ConnectionDoesNotExistError. A fresh session
        # is opened in Phase 3 for writing results.
        async with async_session_factory() as session:
            bt = await session.execute(
                text(
                    """
                    SELECT b.id, b.strategy_id, b.start_date, b.end_date,
                           b.initial_cash, b.benchmark_code, b.params_snapshot,
                           s.source_code, s.name AS strategy_name, s.config,
                           s.user_id
                    FROM backtest_results b
                    JOIN strategies s ON s.id = b.strategy_id
                    WHERE b.id = :id
                    """
                ),
                {"id": backtest_id},
            )
            bt_row = bt.mappings().one_or_none()
            if bt_row is None:
                return {"error": "backtest record not found"}

            await session.execute(
                text("UPDATE backtest_results SET status = 'running', started_at = NOW() WHERE id = :id"),
                {"id": backtest_id},
            )
            await session.commit()

            target = _target_from_snapshot(bt_row.get("params_snapshot"))
            filters = _filters_from_snapshot(bt_row.get("params_snapshot"), target)
            stock_codes = await _resolve_stock_codes(session, bt_row["user_id"], target, bt_row["start_date"], filters)

            if not stock_codes:
                await session.execute(
                    text(
                        "UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"
                    ),
                    {"err": "no stocks available for selected target", "id": backtest_id},
                )
                await session.commit()
                return {"error": "no stocks available for selected target"}

            # Resolve strategy config + trading fee config EARLY, on a fresh
            # connection before the long K-line load. These only depend on the
            # backtest record / user, not on K-lines. Fetching them here avoids
            # running a query on a connection that later idles-in-transaction
            # during the K-line cache serialization (which Postgres kills via
            # idle_in_transaction_session_timeout=30s, surfacing as asyncpg
            # "connection is closed").
            strategy_config = _merge_backtest_config(
                bt_row.get("config"),
                bt_row.get("params_snapshot"),
            )

            global_fee_cfg = await get_trading_fee_config(session, bt_row["user_id"])
            fee_cfg = _merge_fee_config(global_fee_cfg, strategy_config.get("fee_config"))

            risk_cfg = strategy_config.get("risk_config", {})

            # Prepend a warmup window before start_date so technical indicators
            # (MA20/MACD/BOLL/KDJ/Donchian, lookback=60 bars in the engine) are
            # already primed on the first backtest day. Without it the first ~1
            # month produced no trades because the indicator window had too few
            # bars. Execution stays gated to [start_date, end_date] by the engine.
            WARMUP_CALENDAR_DAYS = 180  # ≈ 120 trading days, covers max lookback
            warmup_start = bt_row["start_date"] - timedelta(days=WARMUP_CALENDAR_DAYS)

            # Try Redis cache first, then fall back to DB query.
            # This avoids redundant DB hits when the same stock pool + date
            # range is backtested repeatedly (e.g., strategy parameter tuning).
            cached = await get_cached_klines(stock_codes, warmup_start, bt_row["end_date"])
            if cached is None:
                # Cache miss — batch load raw rows from DB.
                all_klines_raw: dict[str, list[dict[str, Any]]] = {}
                kline_result = await session.execute(
                    text(
                        """
                        SELECT ts_code, trade_date, open, high, low, close, pre_close,
                               volume, amount, turnover_rate, adj_factor, is_suspended,
                               is_limit_up, is_limit_down
                        FROM daily_kline
                        WHERE ts_code = ANY(CAST(:codes AS VARCHAR[]))
                          AND trade_date BETWEEN :start_date AND :end_date
                        ORDER BY ts_code, trade_date
                        """
                    ),
                    {
                        "codes": stock_codes,
                        "start_date": warmup_start,
                        "end_date": bt_row["end_date"],
                    },
                )
                for row in kline_result.mappings().all():
                    row_dict = dict(row)
                    all_klines_raw.setdefault(row_dict["ts_code"], []).append(row_dict)
                # Close the transaction right after the K-line SELECT so the
                # connection is NOT idle-in-transaction during the long Redis
                # serialization below. Postgres' idle_in_transaction_session_timeout
                # (30s) otherwise kills the connection, surfacing as asyncpg
                # "connection is closed" on the next query.
                await session.commit()
                # Cache the raw row dicts (robust against any dataclass/slots
                # pickle round-trip corruption). The engine always receives
                # freshly rebuilt KBar objects via _parse_kline_rows, identical
                # to the non-cached path.
                if all_klines_raw:
                    await set_cached_klines(
                        stock_codes, warmup_start, bt_row["end_date"], all_klines_raw
                    )
                all_klines = {
                    code: _parse_kline_rows(rows)
                    for code, rows in all_klines_raw.items()
                }
            else:
                # Cache hit — rebuild engine-ready KBar objects from raw rows.
                all_klines = {
                    code: _parse_kline_rows(rows)
                    for code, rows in cached.items()
                }

            if not all_klines:
                await session.execute(
                    text(
                        "UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"
                    ),
                    {"err": "no K-line data found for the selected stocks and date range", "id": backtest_id},
                )
                await session.commit()
                return {"error": "no K-line data"}

            # ---- 注入基准 / 额外序列（指数 / 行业 / 板块）到策略 ctx ----
            # 这些序列与股票池用同一张 daily_kline 表、同一套加载逻辑，
            # 故字段完全一致；库中无数据的 code 安静降级（视图为空 / None）。
            benchmark_code = bt_row.get("benchmark_code")
            params_snapshot = bt_row.get("params_snapshot") or {}
            extra_series = params_snapshot.get("extra_series", {}) or {}
            aux_codes = [c for c in ([benchmark_code] if benchmark_code else []) + list(extra_series.values()) if c]
            aux_raw = await _fetch_klines_for_codes(session, aux_codes, warmup_start, bt_row["end_date"])

            def _to_kbars(rows: list[dict[str, Any]] | None) -> list[KBar]:
                return _parse_kline_rows(rows) if rows else []

            benchmark_klines = _to_kbars(aux_raw.get(benchmark_code)) if benchmark_code else None
            if benchmark_klines is not None and len(benchmark_klines) == 0:
                benchmark_klines = None
            extra_klines: dict[str, list[KBar]] = {}
            for _name, _code in extra_series.items():
                _kl = _to_kbars(aux_raw.get(_code))
                if _kl:
                    extra_klines[_name] = _kl

            # ---- 注入基本面（最近一期已公告财报，防前视）----
            # ts_code -> list[dict]（按 announce_date, report_date 升序，adapter 会再排序+二分）
            # 只在 announce_date <= 回测截止日内的财报参与（更早的已被区间过滤，天然防前视）。
            fundamentals: dict[str, list[dict[str, Any]]] = {}
            if all_klines:
                _fund_rows = await session.execute(
                    text("""
                        SELECT ts_code, report_date, announce_date, roe,
                               revenue_growth, net_profit_growth, gross_margin, net_profit
                        FROM stock_fundamentals
                        WHERE ts_code = ANY(:codes)
                          AND announce_date IS NOT NULL AND announce_date <= :end_date
                        ORDER BY ts_code, announce_date, report_date
                    """),
                    {"codes": list(all_klines.keys()), "end_date": bt_row["end_date"]},
                )
                for _fr in _fund_rows.mappings():
                    fundamentals.setdefault(_fr["ts_code"], []).append(dict(_fr))
            if fundamentals:
                logger.info(
                    "backtest %s: loaded fundamentals for %d codes (%d rows)",
                    backtest_id, len(fundamentals), sum(len(v) for v in fundamentals.values()),
                )

            config = BacktestConfig(
                strategy_id=bt_row["strategy_id"],
                source_code=bt_row["source_code"],
                stock_pool=list(all_klines.keys()),
                start_date=bt_row["start_date"],
                end_date=bt_row["end_date"],
                initial_cash=Decimal(str(bt_row["initial_cash"])),
                fee_config=fee_cfg,
                benchmark_code=bt_row.get("benchmark_code"),
                extra_series=extra_series,
                stop_loss_pct=float(risk_cfg.get("stop_loss_pct", 0.0)),
                take_profit_pct=float(risk_cfg.get("take_profit_pct", 0.0)),
                trailing_stop_pct=float(risk_cfg.get("trailing_stop_pct", 0.0)),
                trailing_activation_pct=float(risk_cfg.get("trailing_activation_pct", 0.0)),
               time_stop_days=int(risk_cfg.get("time_stop_days", 0)),
               slippage_pct=float(risk_cfg.get("slippage_pct", 0.001)),
                rebalance_mode=str(strategy_config.get("rebalance_mode", "disabled")),
                max_positions=int(strategy_config.get("max_positions", 0)),
                max_daily_buys=int(strategy_config.get("max_daily_buys", 0)),
                rebalance_version=int(strategy_config.get("rebalance_version", 1)),
                rebalance_frequency=str(strategy_config.get("rebalance_frequency", "weekly")),
                weighting_method=str(strategy_config.get("weighting_method", "equal")),
                rank_buffer_pct=float(strategy_config.get("rank_buffer_pct", 0.2)),
                score_max_age_sessions=int(strategy_config.get("score_max_age_sessions", 5)),
            )

            benchmark_code = bt_row.get("benchmark_code")
            start_date = bt_row["start_date"]
            end_date = bt_row["end_date"]
            # Close the transaction + return the connection to the pool before the
            # long compute so it is never left idle-in-transaction.
            await session.commit()

        # ---- Phase 2: pure-Python backtest run (NO DB access) ----
        results: dict[str, Any] | None = None
        compute_error: str | None = None
        try:
            runner = BacktestRunner(config)
            results = runner.run(
                all_klines,
                benchmark_klines=benchmark_klines,
                extra_klines=extra_klines,
                fundamentals=fundamentals,
            )
        except Exception as exc:
            import traceback as _tb
            compute_error = f"{exc.__class__.__name__}: {exc}\n{_tb.format_exc()}"

        # ---- Phase 3: attach benchmark metrics + write results (fresh session) ----
        # The pure-Python compute above can run for many minutes (complex
        # strategies), during which every pooled asyncpg connection idles and may
        # be closed by the server (idle timeout / TCP keepalive) or go stale
        # past pool_recycle. `pool_pre_ping` alone does not always catch a
        # connection that is closed *after* the ping but *before* statement
        # prepare, surfacing as asyncpg "connection is closed". Disposing the
        # whole pool forces fresh connections for the result-write phase and
        # eliminates that stale-connection failure — independent of any strategy
        # code. We only dispose once, between compute and write.
        await db_engine.dispose()
        async with async_session_factory() as session:
            if results is not None:
                if benchmark_code:
                    bm_rows = await _fetch_benchmark_klines(session, benchmark_code, start_date, end_date)
                    if bm_rows:
                        strategy_dates = [e["date"] for e in results.get("equity_curve", [])]
                        strategy_values = [e["total_asset"] for e in results.get("equity_curve", [])]
                        bm_metrics = _compute_benchmark_metrics(strategy_values, strategy_dates, bm_rows, benchmark_code)
                        if bm_metrics:
                            results["benchmark_metrics"] = bm_metrics
                            if "performance" in results:
                                results["performance"]["benchmark"] = bm_metrics

                engine = "python_native"
                results["engine"] = engine
                if "performance" in results and isinstance(results["performance"], dict):
                    results["performance"].update({
                        "monthly_returns": results.get("monthly_returns", {}),
                        "daily_returns": results.get("daily_returns", []),
                        "pnl_analysis": results.get("pnl_analysis", {}),
                    })
                    results["performance"]["engine"] = engine
                    results["performance"]["filters"] = filters
                    results["performance"]["risk_config"] = results.get("execution_assumptions", {})
                    if results.get("strategy_errors"):
                        results["performance"]["strategy_errors"] = results["strategy_errors"]
                    results["performance"].update(_stock_scope_diagnostics(list(all_klines.keys())))

            if compute_error is not None:
                await session.execute(
                    text(
                        "UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"
                    ),
                    {"err": compute_error, "id": backtest_id},
                )
                await session.commit()
                return {"error": compute_error}

            if (
                results is not None
                and results.get("strategy_errors")
                and not results.get("trade_records")
                and not results.get("signal_log")
            ):
                first_error = results["strategy_errors"][0]
                err_msg = (
                    f"{first_error.get('error_type') or 'StrategyExecutionError'}: "
                    f"{first_error.get('error_message') or 'strategy produced no valid signal'}"
                )
                await session.execute(
                    text(
                        "UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"
                    ),
                    {"err": err_msg, "id": backtest_id},
                )
                await session.commit()
                return {"error": err_msg, "strategy_errors": results["strategy_errors"]}

            await session.execute(
                text(
                    """
                    UPDATE backtest_results SET
                        status = 'success',
                        strategy_source_snapshot = :strategy_source_snapshot,
                        total_return = :total_return,
                        annual_return = :annual_return,
                        sharpe_ratio = :sharpe_ratio,
                        max_drawdown = :max_drawdown,
                        annual_vol = :annual_vol,
                        win_rate = :win_rate,
                        trade_count = :trade_count,
                        performance = CAST(:performance AS JSONB),
                        trade_records = CAST(:trade_records AS JSONB),
                        equity_curve = CAST(:equity_curve AS JSONB),
                        finished_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "strategy_source_snapshot": bt_row["source_code"],
                    "total_return": results["total_return"],
                    "annual_return": results["annual_return"],
                    "sharpe_ratio": results["sharpe_ratio"],
                    "max_drawdown": results["max_drawdown"],
                    "annual_vol": results["annual_vol"],
                    "win_rate": results["win_rate"],
                    "trade_count": results["trade_count"],
                    "performance": json.dumps(results["performance"], ensure_ascii=False, default=str),
                    # trade_records 保持向后兼容：仅存计数占位，明细写入 backtest_trades 表
                    "trade_records": json.dumps(
                        {"count": len(results.get("trade_records", [])), "note": "see backtest_trades table"},
                        ensure_ascii=False,
                    ),
                    "equity_curve": json.dumps(results["equity_curve"], ensure_ascii=False, default=str),
                    "id": backtest_id,
                },
            )

            # 批量写入规范化明细表，避免单值 JSONB 超过 256MB 限制
            await _persist_backtest_details(session, backtest_id, results)

            await session.commit()
            return results

    try:
        return run_async(_run())
    except SoftTimeLimitExceeded:
        # Soft time limit (task_soft_time_limit=1500s) exceeded — graceful cleanup.
        # Mark DB record as failed with explicit reason. Without this handler, Celery
        # would be hard-killed at task_time_limit=1800s leaving status='running'.

        async def _mark_timeout() -> None:
            async with async_session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE backtest_results "
                        "SET status = 'failed', "
                        "    error_message = 'soft time limit exceeded (task_soft_time_limit=1500s)', "
                        "    finished_at = NOW() "
                        "WHERE id = :id AND status = 'running'"
                    ),
                    {"id": backtest_id},
                )
                await session.commit()

        try:
            run_async(_mark_timeout())
        except Exception:
            logger.exception("Failed to mark backtest %s as timed out", backtest_id)
        return {"error": f"backtest {backtest_id} timed out (soft time limit exceeded)"}
    except Exception as exc:
        import traceback
        err_msg = f"unhandled exception in backtest {backtest_id}: {traceback.format_exc()}"
        logger.error(err_msg)

        async def _mark_failed() -> None:
            async with async_session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE backtest_results "
                        "SET status = 'failed', error_message = :err, finished_at = NOW() "
                        "WHERE id = :id AND status IN ('pending', 'running')"
                    ),
                    {"err": err_msg[:2000], "id": backtest_id},
                )
                await session.commit()

        try:
            run_async(_mark_failed())
        except Exception:
            logger.exception("Failed to mark backtest %s as failed after unhandled error", backtest_id)

        return {"error": err_msg}


_BATCH_SIZE = 2000


def _as_date(value: Any) -> "date | None":
    """归一化日期：date/datetime 原样返回；ISO/紧凑字符串解析为 date。

    adapter 在组装 trade_records / closed_lots 时已把日期 isoformat 成字符串，
    而子表日期列为 DATE，asyncpg 不接受裸字符串，直接传入会抛
    AttributeError: 'str'（见回测 #149 写入失败）。这里统一转回 date 对象。
    """
    if value is None:
        return None
    if isinstance(value, (date,)) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                logger.debug("silent except in _as_date")
                continue
    return None


async def _persist_backtest_details(session: AsyncSession, backtest_id: int, results: dict[str, Any]) -> None:
    """批量写入回测明细到规范化子表，规避单值 JSONB 256MB 限制。

    - trade_records -> backtest_trades
    - pnl_analysis.closed_lots -> backtest_closed_lots
    - pnl_analysis.stock_rankings -> backtest_stock_rankings
    """
    trade_records: list[dict[str, Any]] = results.get("trade_records") or []
    closed_lots: list[dict[str, Any]] = results.get("closed_lots") or []
    stock_rankings: list[dict[str, Any]] = results.get("stock_rankings") or []

    # 成交明细
    if trade_records:
        trade_rows = [
            {
                "backtest_id": backtest_id,
                "seq": i,
                "ts_code": t["ts_code"],
                "trade_date": _as_date(t["trade_date"]),
                "direction": t["direction"],
                "price": t["price"],
                "volume": t["volume"],
                "amount": t["amount"],
                "fee": t.get("commission", 0),
                "stamp_tax": t.get("stamp_tax", 0),
                "transfer_fee": t.get("transfer_fee", 0),
                "slippage": 0,
                "signal_type": t.get("signal_type", "") or t.get("action", ""),
                "action": t.get("action", ""),
                "signal_reason": t.get("signal_reason", ""),
                "target_position": t.get("target_position", 0),
                "position_before": t.get("position_before", 0),
                "position_after": t.get("position_after", 0),
                "pnl": t.get("pnl", 0),
                "balance_before": t.get("balance_before", 0),
                "balance_after": t.get("balance_after", 0),
                "holding_days": t.get("holding_days", 0),
                "exit_reason": t.get("exit_reason", ""),
            }
            for i, t in enumerate(trade_records)
        ]
        for start in range(0, len(trade_rows), _BATCH_SIZE):
            batch = trade_rows[start:start + _BATCH_SIZE]
            await session.execute(
                text(
                    """
                    INSERT INTO backtest_trades (
                        backtest_id, seq, ts_code, trade_date, direction, price, volume,
                        amount, fee, stamp_tax, transfer_fee, slippage, signal_type, action,
                        signal_reason, target_position, position_before, position_after,
                        pnl, balance_before, balance_after, holding_days, exit_reason
                    ) VALUES (
                        :backtest_id, :seq, :ts_code, :trade_date, :direction, :price, :volume,
                        :amount, :fee, :stamp_tax, :transfer_fee, :slippage, :signal_type, :action,
                        :signal_reason, :target_position, :position_before, :position_after,
                        :pnl, :balance_before, :balance_after, :holding_days, :exit_reason
                    )
                    """
                ),
                batch,
            )

    # 平仓明细
    if closed_lots:
        lot_rows = [
            {
                "backtest_id": backtest_id,
                "seq": i,
                "ts_code": lot["ts_code"],
                "shares": lot["shares"],
                "entry_price": lot["entry_price"],
                "entry_date": _as_date(lot["entry_date"]),
                "exit_price": lot["exit_price"],
                "exit_date": _as_date(lot["exit_date"]),
                "entry_fee": lot["entry_fee"],
                "exit_fee": lot["exit_fee"],
                "gross_pnl": lot["gross_pnl"],
                "net_pnl": lot["net_pnl"],
                "return_rate": lot["return_rate"],
                "holding_days": lot["holding_days"],
                "exit_reason": lot["exit_reason"],
            }
            for i, lot in enumerate(closed_lots)
        ]
        for start in range(0, len(lot_rows), _BATCH_SIZE):
            batch = lot_rows[start:start + _BATCH_SIZE]
            await session.execute(
                text(
                    """
                    INSERT INTO backtest_closed_lots (
                        backtest_id, seq, ts_code, shares, entry_price, entry_date,
                        exit_price, exit_date, entry_fee, exit_fee, gross_pnl, net_pnl,
                        return_rate, holding_days, exit_reason
                    ) VALUES (
                        :backtest_id, :seq, :ts_code, :shares, :entry_price, :entry_date,
                        :exit_price, :exit_date, :entry_fee, :exit_fee, :gross_pnl, :net_pnl,
                        :return_rate, :holding_days, :exit_reason
                    )
                    """
                ),
                batch,
            )

    # 个股归因排行
    if stock_rankings:
        rank_rows = [
            {
                "backtest_id": backtest_id,
                "seq": i,
                "ts_code": r["ts_code"],
                "trade_count": r.get("closed_lot_count", 0),
                "total_pnl": r.get("net_pnl", 0),
                "win_rate": r.get("win_rate", 0),
                "avg_return": r.get("return_rate", 0),
                "max_profit": r.get("max_profit", 0),
                "max_loss": r.get("max_loss", 0),
            }
            for i, r in enumerate(stock_rankings)
        ]
        for start in range(0, len(rank_rows), _BATCH_SIZE):
            batch = rank_rows[start:start + _BATCH_SIZE]
            await session.execute(
                text(
                    """
                    INSERT INTO backtest_stock_rankings (
                        backtest_id, seq, ts_code, trade_count, total_pnl, win_rate,
                        avg_return, max_profit, max_loss
                    ) VALUES (
                        :backtest_id, :seq, :ts_code, :trade_count, :total_pnl, :win_rate,
                        :avg_return, :max_profit, :max_loss
                    )
                    """
                ),
                batch,
            )
