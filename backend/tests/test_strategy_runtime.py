from datetime import date
from decimal import Decimal

from app.backtest.adapter import BacktestContext
from app.backtest.strategy_runtime import execute_strategy
from tests.conftest import sample_kbar


def _ctx() -> BacktestContext:
    return BacktestContext([sample_kbar(trade_date=date(2026, 5, 21))], {}, Decimal("100000"))


def test_execute_strategy_returns_signal() -> None:
    result = execute_strategy(
        "def generate_signal(ctx):\n    return {'signal_type': '买入', 'target_position': 1.0}",
        _ctx(),
        timeout_seconds=2,
    )

    assert result.ok is True
    assert result.signal == {"signal_type": "买入", "target_position": 1.0}
    assert result.error_type is None


def test_execute_strategy_missing_generate_signal_is_no_signal() -> None:
    result = execute_strategy("x = 1", _ctx(), timeout_seconds=2)

    assert result.ok is True
    assert result.signal is None


def test_execute_strategy_non_dict_is_no_signal() -> None:
    result = execute_strategy("def generate_signal(ctx):\n    return 'hold'", _ctx(), timeout_seconds=2)

    assert result.ok is True
    assert result.signal is None


def test_execute_strategy_returns_structured_exception() -> None:
    result = execute_strategy(
        "def generate_signal(ctx):\n    raise RuntimeError('boom')",
        _ctx(),
        timeout_seconds=2,
    )

    assert result.ok is False
    assert result.error_type == "RuntimeError"
    assert result.error_message == "boom"
    assert result.traceback and "generate_signal" in result.traceback
    assert result.timed_out is False


def test_execute_strategy_times_out_and_terminates_child() -> None:
    result = execute_strategy(
        "def generate_signal(ctx):\n    while True:\n        pass",
        _ctx(),
        timeout_seconds=0.2,
    )

    assert result.ok is False
    assert result.error_type == "StrategyTimeoutError"
    assert result.timed_out is True


def test_execute_strategy_allow_inline_runs_without_child_process(monkeypatch) -> None:
    from app.backtest import strategy_runtime

    def fail_process(*_args, **_kwargs):
        raise AssertionError("inline execution must not spawn a child process")

    monkeypatch.setattr(strategy_runtime.multiprocessing, "get_context", fail_process)

    result = execute_strategy(
        "def generate_signal(ctx):\n    return {'signal_type': '观望'}",
        _ctx(),
        allow_inline=True,
    )

    assert result.ok is True
    assert result.signal == {"signal_type": "观望"}
