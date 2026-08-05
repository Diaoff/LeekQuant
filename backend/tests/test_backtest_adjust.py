"""Tests for P1-A: adj_factor filling + backtest qfq adjustment.

Verifies that:
1. Providers fetch前复权 (qfq) prices by default
2. adj_factor is plumbed through normalize_daily_kline
3. BacktestRunner._adjust_price is a no-op in qfq mode (prices already adjusted)
4. _calc_total_asset applies _adjust_price (no-op for qfq, but API contract)
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest

from app.backtest.adapter import BacktestConfig, BacktestRunner, KBar, Position
from app.data.models import DailyKline
from app.data.normalizers import normalize_daily_kline
from app.data import providers as providers_module
from app.data.providers import (
    ADataProvider,
    AkShareProvider,
    BaostockProvider,
    EastMoneyHttpProvider,
)


# ---------------------------------------------------------------------------
# _adjust_price unit tests
# ---------------------------------------------------------------------------


class TestAdjustPrice:
    """_adjust_price should be a no-op when prices are already qfq-adjusted."""

    @pytest.mark.parametrize(
        "mode,adj_factor,price,expected",
        [
            ("qfq", Decimal("1.0"), Decimal("10.50"), Decimal("10.50")),
            ("qfq", Decimal("1.5"), Decimal("10.50"), Decimal("10.50")),  # NOT multiplied
            ("qfq", None, Decimal("10.50"), Decimal("10.50")),
            ("none", Decimal("1.5"), Decimal("10.50"), Decimal("10.50")),
            ("none", None, Decimal("10.50"), Decimal("10.50")),
        ],
    )
    def test_adjust_price_is_noop_for_qfq_and_none_modes(
        self, mode: str, adj_factor: Decimal | None, price: Decimal, expected: Decimal
    ) -> None:
        """In qfq mode, providers already return adjusted prices, so
        multiplying by adj_factor would double-adjust. _adjust_price
        must return price unchanged. Same for 'none' mode (no adjustment).
        """
        result = BacktestRunner._adjust_price(price, adj_factor, mode)
        assert result == expected, f"mode={mode}, adj_factor={adj_factor}: expected {expected}, got {result}"

    def test_adjust_price_returns_decimal_type(self) -> None:
        """Type contract: must return Decimal (not float)."""
        result = BacktestRunner._adjust_price(Decimal("10.50"), Decimal("1.5"), "qfq")
        assert isinstance(result, Decimal)


# ---------------------------------------------------------------------------
# _calc_total_asset integration test
# ---------------------------------------------------------------------------


class TestCalcTotalAsset:
    """_calc_total_asset must use _adjust_price on close prices."""

    def test_calc_total_asset_uses_adjusted_close_in_qfq_mode(self) -> None:
        """When position is held and adj_factor is non-trivial, total asset
        must use the qfq close price directly (no double-adjustment).

        Setup: 1000 shares @ close=10.50 (already qfq), adj_factor=1.5
        Expected position_value = 10.50 * 1000 = 10500.0
        (NOT 10.50 * 1.5 * 1000 = 15750.0, which would double-adjust)
        """
        config = BacktestConfig(
            strategy_id=1,
            source_code="",
            stock_pool=["000001.SZ"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 5),
            initial_cash=Decimal("100000"),
        )
        runner = BacktestRunner(config)
        # Simulate holding 1000 shares
        runner.positions["000001.SZ"] = Position(
            ts_code="000001.SZ", shares=1000, avg_cost=Decimal("10.00")
        )

        # KBar with non-trivial adj_factor (would cause double-adjustment
        # if _adjust_price multiplied)
        kbar = KBar(
            ts_code="000001.SZ",
            trade_date=date(2026, 5, 1),
            open=Decimal("10.40"),
            high=Decimal("10.60"),
            low=Decimal("10.30"),
            close=Decimal("10.50"),
            pre_close=Decimal("10.40"),
            volume=100000,
            amount=Decimal("1050000"),
            adj_factor=Decimal("1.5"),
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
            turnover_rate=None,
        )
        all_klines = {"000001.SZ": [kbar]}

        total = runner._calc_total_asset(all_klines, date(2026, 5, 1))

        # Expected: 100000 (cash) + 10.50 * 1000 (position) = 110500.0
        # If _adjust_price multiplied by 1.5, we'd get 115750.0 (wrong)
        assert total == Decimal("110500.0"), (
            f"expected 110500.0 (no double-adjust), got {total}"
        )

    def test_calc_total_asset_handles_none_adj_factor(self) -> None:
        """adj_factor=None (AData/EastMoney providers) must not break."""
        config = BacktestConfig(
            strategy_id=1,
            source_code="",
            stock_pool=["000001.SZ"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 5),
            initial_cash=Decimal("100000"),
        )
        runner = BacktestRunner(config)
        runner.positions["000001.SZ"] = Position(
            ts_code="000001.SZ", shares=500, avg_cost=Decimal("10.00")
        )

        kbar = KBar(
            ts_code="000001.SZ",
            trade_date=date(2026, 5, 1),
            open=Decimal("10.40"),
            high=Decimal("10.60"),
            low=Decimal("10.30"),
            close=Decimal("10.50"),
            pre_close=Decimal("10.40"),
            volume=100000,
            amount=Decimal("1050000"),
            adj_factor=None,
            is_suspended=False,
            is_limit_up=False,
            is_limit_down=False,
            turnover_rate=None,
        )
        all_klines = {"000001.SZ": [kbar]}

        total = runner._calc_total_asset(all_klines, date(2026, 5, 1))
        # 100000 + 10.50 * 500 = 105250.0
        assert total == Decimal("105250.0")


# ---------------------------------------------------------------------------
# normalize_daily_kline adj_factor plumbing
# ---------------------------------------------------------------------------


class TestNormalizeAdjFactor:
    """normalize_daily_kline must preserve adj_factor from provider rows."""

    def test_normalize_extracts_adj_factor_from_row(self) -> None:
        """adj_factor field should flow through normalize_daily_kline."""
        row = {
            "trade_date": "2026-05-18",
            "open": "10.00",
            "high": "10.50",
            "low": "9.90",
            "close": "10.30",
            "volume": "100000",
            "amount": "1030000.00",
            "adj_factor": "1.234",
        }
        kline = normalize_daily_kline(row, "baostock", ts_code="600000.SH")
        assert kline.adj_factor == Decimal("1.234")
        assert kline.ts_code == "600000.SH"

    def test_normalize_returns_none_adj_factor_when_missing(self) -> None:
        """When provider doesn't expose adj_factor (AData/EastMoney),
        normalize should return None — not raise."""
        row = {
            "trade_date": "2026-05-18",
            "open": "10.00",
            "high": "10.50",
            "low": "9.90",
            "close": "10.30",
            "volume": "100000",
            "amount": "1030000.00",
        }
        kline = normalize_daily_kline(row, "adata", ts_code="600000.SH")
        assert kline.adj_factor is None

    def test_normalize_accepts_chinese_alias_for_adj_factor(self) -> None:
        """复权因子 Chinese alias should also work."""
        row = {
            "trade_date": "2026-05-18",
            "open": "10.00",
            "high": "10.50",
            "low": "9.90",
            "close": "10.30",
            "volume": "100000",
            "amount": "1030000.00",
            "复权因子": "2.0",
        }
        kline = normalize_daily_kline(row, "baostock", ts_code="600000.SH")
        assert kline.adj_factor == Decimal("2.0")


# ---------------------------------------------------------------------------
# Provider API parameter tests (qfq configuration)
# ---------------------------------------------------------------------------


class TestProvidersFetchQfqPrices:
    """All 4 providers must fetch前复权 (qfq) prices by default.

    Verifies the API call parameters that fix the ex-dividend fake-阴线 bug.
    """

    def test_adata_provider_passes_adj_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ADataProvider.fetch_daily_kline must pass adj=True to adata."""
        captured: dict[str, Any] = {}

        class FakeAdataModule:
            class stock:
                class market:
                    @staticmethod
                    def get_market(**kwargs):
                        captured.update(kwargs)
                        import pandas as pd
                        return pd.DataFrame()

        monkeypatch.setitem(__import__("sys").modules, "adata", FakeAdataModule)

        ADataProvider().fetch_daily_kline("600000.SH", date(2026, 5, 1), date(2026, 5, 5))

        assert captured.get("adj") is True, (
            f"ADataProvider must pass adj=True for qfq, got kwargs={captured}"
        )

    def test_baostock_provider_uses_adjustflag_2_and_adj_factor_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BaostockProvider.fetch_daily_kline must use adjustflag='2' (qfq)
        and include adj_factor in the fields list."""
        import sys

        captured: dict[str, Any] = {}

        class FakeResult:
            def __init__(self):
                self.error_code = "0"
                self.fields = ["date", "code", "adj_factor"]
                self._rows = [
                    {"date": "2026-05-01", "code": "sh.600000", "adj_factor": "1.0"},
                ]
                self._idx = 0

            def next(self) -> bool:
                if self._idx < len(self._rows):
                    self._idx += 1
                    return True
                return False

            def get_row_data(self):
                return list(self._rows[self._idx - 1].values())

        class FakeBs:
            @staticmethod
            def login():
                class R:
                    error_code = "0"
                    error_msg = ""
                return R()

            @staticmethod
            def logout():
                pass

            @staticmethod
            def query_history_k_data_plus(code, fields, **kwargs):
                captured["code"] = code
                captured["fields"] = fields
                captured.update(kwargs)
                return FakeResult()

        baostock_module = ModuleType("baostock")
        baostock_module.login = FakeBs.login
        baostock_module.logout = FakeBs.logout
        baostock_module.query_history_k_data_plus = FakeBs.query_history_k_data_plus
        monkeypatch.setitem(sys.modules, "baostock", baostock_module)

        BaostockProvider().fetch_daily_kline("600000.SH", date(2026, 5, 1), date(2026, 5, 5))

        assert captured.get("adjustflag") == "2", (
            f"BaostockProvider must use adjustflag='2' (qfq), got {captured.get('adjustflag')}"
        )
        assert "adj_factor" in captured.get("fields", ""), (
            f"BaostockProvider fields must include adj_factor, got {captured.get('fields')}"
        )

    def test_akshare_provider_uses_adjust_qfq(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AkShareProvider.fetch_daily_kline must pass adjust='qfq'."""
        import sys

        captured: dict[str, Any] = {}

        class FakeAkModule:
            class stock:
                @staticmethod
                def zh_a_hist(**kwargs):
                    captured.update(kwargs)
                    import pandas as pd
                    return pd.DataFrame()

        # akshare is accessed as `ak.stock_zh_a_hist` — bind accordingly
        fake_ak = ModuleType("akshare")
        fake_ak.stock_zh_a_hist = lambda **kwargs: captured.update(kwargs) or __import__(
            "pandas"
        ).DataFrame()
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)

        AkShareProvider().fetch_daily_kline("600000.SH", date(2026, 5, 1), date(2026, 5, 5))

        assert captured.get("adjust") == "qfq", (
            f"AkShareProvider must pass adjust='qfq', got {captured.get('adjust')}"
        )

    def test_eastmoney_provider_uses_fqt_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EastMoneyHttpProvider.fetch_daily_kline must pass fqt='1' (qfq)."""
        captured: dict[str, Any] = {}

        def fake_http_json(_url, params=None, _timeout=15):
            captured.update(params or {})
            return {"data": {"klines": []}}

        monkeypatch.setattr(providers_module, "_http_json", fake_http_json)

        EastMoneyHttpProvider().fetch_daily_kline(
            "000001.SZ", date(2026, 5, 1), date(2026, 5, 5)
        )

        assert captured.get("fqt") == "1", (
            f"EastMoneyHttpProvider must use fqt='1' (qfq), got {captured.get('fqt')}"
        )


# ---------------------------------------------------------------------------
# Config field test
# ---------------------------------------------------------------------------


class TestBacktestAdjustModeConfig:
    """BACKTEST_ADJUST_MODE config field validation."""

    def test_default_is_qfq(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default value should be 'qfq'."""
        # Force re-import to reset lru_cache
        import importlib
        import app.core.config as config_module

        # Clear lru_cache to ensure fresh Settings
        config_module.get_settings.cache_clear()
        try:
            # Ensure no env override
            monkeypatch.delenv("BACKTEST_ADJUST_MODE", raising=False)
            settings = config_module.Settings(
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db"
            )
            assert settings.backtest_adjust_mode == "qfq"
        finally:
            config_module.get_settings.cache_clear()

    @pytest.mark.parametrize("valid_mode", ["qfq", "hfq", "none", "QFQ", " None "])
    def test_valid_modes_accepted(self, valid_mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid mode values should be accepted (case-insensitive, trimmed)."""
        import app.core.config as config_module

        config_module.get_settings.cache_clear()
        try:
            monkeypatch.setenv("BACKTEST_ADJUST_MODE", valid_mode)
            settings = config_module.Settings(
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db"
            )
            assert settings.backtest_adjust_mode == valid_mode.strip().lower()
        finally:
            config_module.get_settings.cache_clear()
            monkeypatch.delenv("BACKTEST_ADJUST_MODE", raising=False)

    def test_invalid_mode_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid mode values should raise ValueError."""
        import app.core.config as config_module

        config_module.get_settings.cache_clear()
        try:
            monkeypatch.setenv("BACKTEST_ADJUST_MODE", "invalid_mode")
            with pytest.raises(ValueError, match="BACKTEST_ADJUST_MODE"):
                config_module.Settings(
                    DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db"
                )
        finally:
            config_module.get_settings.cache_clear()
            monkeypatch.delenv("BACKTEST_ADJUST_MODE", raising=False)
