"""Celery tasks for backtest execution."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar
from app.backtest.cost import FeeConfig
from app.db.session import async_session_factory
from app.tasks.celery_app import celery_app


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
                    SELECT b.id, b.strategy_id, b.pool_id, b.start_date, b.end_date,
                           b.initial_cash, b.benchmark_code,
                           s.source_code, s.name AS strategy_name, s.config,
                           p.name AS pool_name
                    FROM backtest_results b
                    JOIN strategies s ON s.id = b.strategy_id
                    LEFT JOIN stock_pools p ON p.id = b.pool_id
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

            stock_codes = []
            if bt_row["pool_id"]:
                pool_result = await session.execute(
                    text("SELECT ts_code FROM stock_pool_items WHERE pool_id = :pool_id"),
                    {"pool_id": bt_row["pool_id"]},
                )
                stock_codes = [r["ts_code"] for r in pool_result.mappings().all()]

            if not stock_codes:
                default_result = await session.execute(
                    text(
                        """
                        SELECT ts_code FROM stock_basic
                        WHERE is_delisted = FALSE
                        ORDER BY symbol
                        """
                    )
                )
                stock_codes = [r["ts_code"] for r in default_result.mappings().all()]

            if not stock_codes:
                await session.execute(
                    text(
                        "UPDATE backtest_results SET status = 'failed', error_message = :err, finished_at = NOW() WHERE id = :id"
                    ),
                    {"err": "no stocks available for backtest", "id": backtest_id},
                )
                await session.commit()
                return {"error": "no stocks available"}

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

            strategy_config = bt_row.get("config") or {}
            if isinstance(strategy_config, str):
                import json
                strategy_config = json.loads(strategy_config)

            fee_cfg_dict = strategy_config.get("fee_config", {})
            if fee_cfg_dict:
                fee_cfg = FeeConfig(
                    commission_rate=Decimal(str(fee_cfg_dict.get("commission_rate", FeeConfig.commission_rate))),
                    min_commission=Decimal(str(fee_cfg_dict.get("min_commission", FeeConfig.min_commission))),
                    stamp_tax_rate=Decimal(str(fee_cfg_dict.get("stamp_tax_rate", FeeConfig.stamp_tax_rate))),
                    transfer_fee_rate=Decimal(str(fee_cfg_dict.get("transfer_fee_rate", FeeConfig.transfer_fee_rate))),
                )
            else:
                fee_cfg = FeeConfig()

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
            )

            try:
                # Engine selection: Hikyuu preferred, Python fallback
                use_hikyuu = False
                try:
                    from app.backtest.hikyuu_adapter import HikyuuBacktestAdapter, HIKYUU_AVAILABLE
                    if HIKYUU_AVAILABLE:
                        adapter = HikyuuBacktestAdapter(session)
                        hikyuu_config = {
                            "strategy_id": bt_row["strategy_id"],
                            "source_code": bt_row["source_code"],
                            "stock_pool": list(all_klines.keys()),
                            "start_date": bt_row["start_date"],
                            "end_date": bt_row["end_date"],
                            "initial_cash": Decimal(str(bt_row["initial_cash"])),
                            "fee_config": fee_cfg,
                            "benchmark_code": bt_row.get("benchmark_code"),
                        }
                        results = adapter.run(hikyuu_config)
                        use_hikyuu = True
                except ImportError as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Hikyuu not available (%s), falling back to Python engine for backtest %s",
                        e, backtest_id,
                    )
                    use_hikyuu = False

                if not use_hikyuu:
                    # Fallback to Python-native BacktestRunner
                    runner = BacktestRunner(config)
                    results = runner.run(all_klines)

                # Tag result with engine identifier
                engine = "hikyuu" if use_hikyuu else "python"
                results["engine"] = engine
                if "performance" in results and isinstance(results["performance"], dict):
                    results["performance"]["engine"] = engine
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

            import json

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

    return asyncio.run(_run())
