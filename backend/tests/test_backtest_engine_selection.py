"""Regression tests for Python-native backtest engine selection."""
from datetime import date
from decimal import Decimal

from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar


def generate_klines(base_price: float = 10.0, days: int = 20, **flags) -> list[KBar]:
    start = date(2026, 1, 2)
    return [
        KBar(
            ts_code="000001.SZ",
            trade_date=start.replace(day=min(d, 28)),
            open=Decimal(str(base_price)),
            high=Decimal(str(base_price * 1.02)),
            low=Decimal(str(base_price * 0.98)),
            close=Decimal(str(base_price * (1 + 0.001 * d))),
            pre_close=Decimal(str(base_price)),
            volume=10000,
            amount=Decimal(str(base_price * 10000)),
            adj_factor=Decimal("1"),
            is_suspended=flags.get("is_suspended", False),
            is_limit_up=False,
            is_limit_down=False,
            turnover_rate=None,
        )
        for d in range(1, days + 1)
    ]


def test_python_native_engine_runs_without_optional_adapter() -> None:
    buy_strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
    config = BacktestConfig(
        strategy_id=1,
        source_code=buy_strategy,
        stock_pool=["000001.SZ"],
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 15),
        initial_cash=Decimal("100000"),
    )

    result = BacktestRunner(config).run({"000001.SZ": generate_klines(days=15)})

    assert result["trade_count"] >= 1
    assert "performance" in result
    assert "trade_records" in result
    assert "equity_curve" in result


def test_python_native_result_format_has_required_fields() -> None:
    buy_strategy = '''
def generate_signal(ctx):
    return {"signal_type": "买入", "current_position": 0, "target_position": 1.0}
'''
    config = BacktestConfig(
        strategy_id=1,
        source_code=buy_strategy,
        stock_pool=["000001.SZ"],
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 15),
        initial_cash=Decimal("100000"),
    )

    result = BacktestRunner(config).run({"000001.SZ": generate_klines(days=15)})

    required_fields = [
        "total_return",
        "annual_return",
        "sharpe_ratio",
        "max_drawdown",
        "annual_vol",
        "win_rate",
        "trade_count",
        "performance",
        "trade_records",
        "equity_curve",
    ]
    for field in required_fields:
        assert field in result
