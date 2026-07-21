"""Celery tasks for backtest execution."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar
from app.backtest.cost import FeeConfig, build_fee_config
from app.db.session import async_session_factory
from app.preferences.service import get_trading_fee_config
from app.tasks.celery_app import celery_app

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
            continue
    return False


def _stock_scope_diagnostics(stock_codes: list[str]) -> dict[str, Any]:
    return {
        "stock_count": len(stock_codes),
    }


async def _factor_scores_by_date(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    stock_codes: list[str],
) -> dict[date, dict[str, dict[str, Any]]]:
    if not stock_codes:
        return {}
    result = await session.execute(
        text(
            """
            SELECT trade_date, ts_code, total_score, rank
            FROM scoring_rank
            WHERE scope_type = 'all'
              AND scope_value IS NULL
              AND trade_date BETWEEN :start_date AND :end_date
              AND ts_code = ANY(CAST(:stock_codes AS VARCHAR[]))
            """
        ),
        {
            "start_date": start_date,
            "end_date": end_date,
            "stock_codes": stock_codes,
        },
    )
    scores: dict[date, dict[str, dict[str, Any]]] = {}
    for row in result.mappings().all():
        trade_date = row["trade_date"]
        if not isinstance(trade_date, date):
            trade_date = date.fromisoformat(str(trade_date))
        scores.setdefault(trade_date, {})[row["ts_code"]] = {
            "total_score": row["total_score"],
            "rank": row["rank"],
        }
    return scores


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
    import asyncio

    async def _run() -> dict[str, Any]:
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

            all_klines: dict[str, list[KBar]] = {}
            for code in stock_codes:
                kline_result = await session.execute(
                    text(
                        """
                        SELECT ts_code, trade_date, open, high, low, close, pre_close,
                               volume, amount, adj_factor, is_suspended,
                               is_limit_up, is_limit_down
                        FROM daily_kline
                        WHERE ts_code = :ts_code
                          AND trade_date BETWEEN :start_date AND :end_date
                        ORDER BY trade_date
                        """
                    ),
                    {
                        "ts_code": code,
                        "start_date": bt_row["start_date"],
                        "end_date": bt_row["end_date"],
                    },
                )
                rows = [dict(r) for r in kline_result.mappings().all()]
                if rows:
                    all_klines[code] = _parse_kline_rows(rows)

            if not all_klines:
                await session.execute(
                    text(
                        "UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"
                    ),
                    {"err": "no K-line data found for the selected stocks and date range", "id": backtest_id},
                )
                await session.commit()
                return {"error": "no K-line data"}

            strategy_config = _merge_backtest_config(
                bt_row.get("config"),
                bt_row.get("params_snapshot"),
            )

            global_fee_cfg = await get_trading_fee_config(session, bt_row["user_id"])
            fee_cfg = _merge_fee_config(global_fee_cfg, strategy_config.get("fee_config"))
            factor_scores = await _factor_scores_by_date(
                session,
                start_date=bt_row["start_date"],
                end_date=bt_row["end_date"],
                stock_codes=list(all_klines.keys()),
            )

            risk_cfg = strategy_config.get("risk_config", {})
            config = BacktestConfig(
                strategy_id=bt_row["strategy_id"],
                source_code=bt_row["source_code"],
                stock_pool=list(all_klines.keys()),
                start_date=bt_row["start_date"],
                end_date=bt_row["end_date"],
                initial_cash=Decimal(str(bt_row["initial_cash"])),
                fee_config=fee_cfg,
                benchmark_code=bt_row.get("benchmark_code"),
                stop_loss_pct=float(risk_cfg.get("stop_loss_pct", 0.0)),
                take_profit_pct=float(risk_cfg.get("take_profit_pct", 0.0)),
                trailing_stop_pct=float(risk_cfg.get("trailing_stop_pct", 0.0)),
                trailing_activation_pct=float(risk_cfg.get("trailing_activation_pct", 0.0)),
                time_stop_days=int(risk_cfg.get("time_stop_days", 0)),
                slippage_pct=float(risk_cfg.get("slippage_pct", 0.001)),
                factor_scores_by_date=factor_scores,
            )

            try:
                runner = BacktestRunner(config)
                results = runner.run(all_klines)

                benchmark_code = bt_row.get("benchmark_code")
                if benchmark_code:
                    bm_rows = await _fetch_benchmark_klines(session, benchmark_code, bt_row["start_date"], bt_row["end_date"])
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
                    results["performance"]["engine"] = engine
                    results["performance"]["filters"] = filters
                    results["performance"]["risk_config"] = results.get("execution_assumptions", {})
                    if results.get("strategy_errors"):
                        results["performance"]["strategy_errors"] = results["strategy_errors"]
                    results["performance"].update(_stock_scope_diagnostics(list(all_klines.keys())))

                if results.get("strategy_errors") and not results.get("trade_records") and not results.get("signal_log"):
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
            except Exception as exc:
                import traceback
                err_msg = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
                await session.execute(
                    text(
                        "UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"
                    ),
                    {"err": err_msg, "id": backtest_id},
                )
                await session.commit()
                return {"error": err_msg}

            await session.execute(
                text(
                    """
                    UPDATE backtest_results SET
                        status = 'success',
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
                    "total_return": results["total_return"],
                    "annual_return": results["annual_return"],
                    "sharpe_ratio": results["sharpe_ratio"],
                    "max_drawdown": results["max_drawdown"],
                    "annual_vol": results["annual_vol"],
                    "win_rate": results["win_rate"],
                    "trade_count": results["trade_count"],
                    "performance": json.dumps(results["performance"], ensure_ascii=False, default=str),
                    "trade_records": json.dumps(results["trade_records"], ensure_ascii=False, default=str),
                    "equity_curve": json.dumps(results["equity_curve"], ensure_ascii=False, default=str),
                    "id": backtest_id,
                },
            )
            await session.commit()
            return results

    try:
        return asyncio.run(_run())
    except SoftTimeLimitExceeded:
        # Soft time limit (task_soft_time_limit=1500s) exceeded — graceful cleanup.
        # Mark DB record as failed with explicit reason. Without this handler, Celery
        # would be hard-killed at task_time_limit=1800s leaving status='running'.
        import asyncio as _asyncio

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
            _asyncio.run(_mark_timeout())
        except Exception:
            pass
        return {"error": f"backtest {backtest_id} timed out (soft time limit exceeded)"}
    except Exception as exc:
        import traceback
        return {"error": f"unhandled exception in backtest {backtest_id}: {traceback.format_exc()}"}
