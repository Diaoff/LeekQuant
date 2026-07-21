from datetime import date
from decimal import Decimal

import pytest

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
    # This test specifically exercises spawn-mode timeout enforcement.
    # Inline mode (the new default) has no process-level timeout and would hang
    # on an infinite loop, so we explicitly request spawn mode here.
    result = execute_strategy(
        "def generate_signal(ctx):\n    while True:\n        pass",
        _ctx(),
        timeout_seconds=0.2,
        allow_inline=False,
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


def test_execute_strategy_daemon_process_runs_inline(monkeypatch) -> None:
    from app.backtest import strategy_runtime

    def fail_process(*_args, **_kwargs):
        raise AssertionError("daemon execution must not spawn a child process")

    class DaemonProcess:
        daemon = True

    monkeypatch.setattr(strategy_runtime.multiprocessing, "current_process", lambda: DaemonProcess())
    monkeypatch.setattr(strategy_runtime.multiprocessing, "get_context", fail_process)

    result = execute_strategy(
        "def generate_signal(ctx):\n    return {'signal_type': '观望'}",
        _ctx(),
    )

    assert result.ok is True
    assert result.signal == {"signal_type": "观望"}


@pytest.mark.parametrize(
    "source_code",
    [
        "def generate_signal(ctx):\n    open('/etc/hosts').read()\n    return {'signal_type': '观望'}",
        "import os\n\ndef generate_signal(ctx):\n    return {'signal_type': '观望'}",
        "def generate_signal(ctx):\n    __import__('os')\n    return {'signal_type': '观望'}",
        "def generate_signal(ctx):\n    eval('1 + 1')\n    return {'signal_type': '观望'}",
        "def generate_signal(ctx):\n    compile('1 + 1', '<strategy>', 'eval')\n    return {'signal_type': '观望'}",
    ],
)
def test_execute_strategy_rejects_dangerous_builtins(source_code) -> None:
    result = execute_strategy(source_code, _ctx(), timeout_seconds=2)

    assert result.ok is False
    assert result.error_type in {"NameError", "ImportError"}
    assert result.signal is None
    assert result.timed_out is False


def test_execute_strategy_allows_safe_builtins() -> None:
    result = execute_strategy(
        "def generate_signal(ctx):\n"
        "    score = sum(range(4)) + max([1, 2, 3]) + len(list(zip([1], [2])))\n"
        "    return {'signal_type': '买入' if score == 10 else '观望'}",
        _ctx(),
        timeout_seconds=2,
    )

    assert result.ok is True
    assert result.signal == {"signal_type": "买入"}
