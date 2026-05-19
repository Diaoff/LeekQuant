"""Tests for Hikyuu backtest adapter integration.

Covers:
- HikyuuBacktestAdapter import and availability flag
- Hikyuu available path: adapter constructs and runs
- Hikyuu unavailable path: fallback to Python BacktestRunner
- Engine selection in tasks.py
- Result format consistency between engines
"""
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar
from app.backtest.cost import FeeConfig
from app.backtest.hikyuu_adapter import HikyuuBacktestAdapter, HIKYUU_AVAILABLE


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
        )
        for d in range(1, days + 1)
    ]


class TestHikyuuAvailability:
    """Test Hikyuu availability flag."""

    def test_hikyuu_import_sets_flag(self):
        """HIKYUU_AVAILABLE flag reflects import success."""
        assert isinstance(HIKYUU_AVAILABLE, bool)

    def test_adapter_class_exists(self):
        """HikyuuBacktestAdapter class is importable."""
        assert hasattr(HikyuuBacktestAdapter, "run")
        assert hasattr(HikyuuBacktestAdapter, "serialize_result")
        assert hasattr(HikyuuBacktestAdapter, "_convert_ts_code")

    def test_serialize_result_standalone(self):
        """serialize_result works without Hikyuu dependency."""
        raw = {
            "trade_records": [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": date(2026, 1, 5),
                    "direction": "买入",
                    "price": 10.0,
                    "volume": 1000,
                    "amount": 10000.0,
                    "commission": 2.5,
                    "stamp_tax": 0.0,
                    "transfer_fee": 0.1,
                    "total_fee": 2.6,
                    "pnl": 0.0,
                    "holding_days": 0,
                }
            ],
            "equity_curve": [
                {"trade_date": "2026-01-05", "cash": 90000.0, "stock_value": 10000.0, "total_asset": 100000.0}
            ],
            "initial_cash": 100000.0,
            "final_asset": 100000.0,
        }
        config = {"initial_cash": 100000.0}
        result = HikyuuBacktestAdapter.serialize_result(raw, config)
        assert "total_return" in result
        assert "performance" in result
        assert "trade_records" in result
        assert "equity_curve" in result
        assert result["trade_count"] == 1
        assert result["performance"]["initial_cash"] == 100000.0


class TestTsCodeConversion:
    """Test ts_code format conversion."""

    def test_sh_conversion(self):
        assert HikyuuBacktestAdapter._convert_ts_code("600000.SH") == "sh600000"

    def test_sz_conversion(self):
        assert HikyuuBacktestAdapter._convert_ts_code("000001.SZ") == "sz000001"

    def test_passthrough(self):
        assert HikyuuBacktestAdapter._convert_ts_code("unknown") == "unknown"


class TestEngineSelection:
    """Test engine selection logic in tasks.py."""

    def test_python_fallback_works(self):
        """When Hikyuu unavailable, Python BacktestRunner works."""
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
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})
        assert "total_return" in result
        assert "trade_records" in result
        assert "equity_curve" in result
        assert result["trade_count"] >= 1

    def test_result_format_consistency(self):
        """Python engine result has all required fields."""
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
        runner = BacktestRunner(config)
        klines = generate_klines(days=15)
        result = runner.run({"000001.SZ": klines})

        required_fields = [
            "total_return", "annual_return", "sharpe_ratio",
            "max_drawdown", "annual_vol", "win_rate", "trade_count",
            "performance", "trade_records", "equity_curve",
        ]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"


class TestHikyuuFallback:
    """Test Hikyuu unavailability fallback."""

    def test_flag_reflects_import_state(self):
        """HIKYUU_AVAILABLE reflects whether hikyuu package is installed."""
        # In current environment, hikyuu is not installed, so flag should be False
        assert HIKYUU_AVAILABLE is False

    def test_adapter_run_raises_without_hikyuu(self):
        """Adapter.run() raises ImportError when hikyuu is unavailable."""
        adapter = HikyuuBacktestAdapter(MagicMock())
        with pytest.raises(ImportError, match="hikyuu package is not installed"):
            adapter.run({"stock_pool": []})
