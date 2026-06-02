"""Isolated runtime for user-authored strategy signal code."""
from __future__ import annotations

import math
import multiprocessing
import os
import sys
import time
import traceback as traceback_mod
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from app.core.config import get_settings


class StrategyExecutionError(RuntimeError):
    """Base error for strategy runtime failures."""


class StrategyTimeoutError(StrategyExecutionError):
    """Raised internally when strategy execution exceeds the configured timeout."""


@dataclass(slots=True)
class StrategyExecutionOptions:
    timeout_seconds: float
    memory_mb: int
    traceback_chars: int


@dataclass(slots=True)
class StrategyExecutionResult:
    ok: bool
    signal: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    duration_ms: int = 0
    timed_out: bool = False

    def error_summary(self) -> str:
        if self.error_type and self.error_message:
            return f"{self.error_type}: {self.error_message}"
        return self.error_type or self.error_message or "strategy execution failed"

    def to_error_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "traceback": self.traceback,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
        }


def _runtime_options(timeout_seconds: float | None = None) -> StrategyExecutionOptions:
    try:
        settings = get_settings()
    except Exception:
        fallback_timeout = float(os.getenv("STRATEGY_EXEC_TIMEOUT_SECONDS", "2.0"))
        return StrategyExecutionOptions(
            timeout_seconds=float(timeout_seconds if timeout_seconds is not None else fallback_timeout),
            memory_mb=int(os.getenv("STRATEGY_EXEC_MEMORY_MB", "256")),
            traceback_chars=int(os.getenv("STRATEGY_EXEC_TRACEBACK_CHARS", "4000")),
        )
    return StrategyExecutionOptions(
        timeout_seconds=float(timeout_seconds if timeout_seconds is not None else settings.strategy_exec_timeout_seconds),
        memory_mb=int(settings.strategy_exec_memory_mb),
        traceback_chars=int(settings.strategy_exec_traceback_chars),
    )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(limit - 20, 0)] + "\n...<truncated>"


def _apply_resource_limits(options: StrategyExecutionOptions) -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - platform dependent.
        return

    try:
        cpu_seconds = max(int(math.ceil(options.timeout_seconds)) + 1, 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except Exception:
        pass

    if not sys.platform.startswith("linux"):
        return

    try:
        memory_bytes = int(options.memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except Exception:
        pass


def _child_main(conn: Connection, source_code: str, ctx: Any, options: StrategyExecutionOptions) -> None:
    started = time.perf_counter()
    try:
        _apply_resource_limits(options)
        from app.libs import MyTT

        sandbox: dict[str, Any] = {"ctx": ctx}
        for name in dir(MyTT):
            if not name.startswith("_"):
                sandbox[name] = getattr(MyTT, name)

        exec(source_code, sandbox)
        func = sandbox.get("generate_signal")
        if func is None:
            conn.send(
                StrategyExecutionResult(
                    ok=True,
                    signal=None,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            return

        result = func(ctx)
        conn.send(
            StrategyExecutionResult(
                ok=True,
                signal=result if isinstance(result, dict) else None,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )
    except BaseException as exc:
        tb = _truncate(traceback_mod.format_exc(), options.traceback_chars)
        try:
            conn.send(
                StrategyExecutionResult(
                    ok=False,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    traceback=tb,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    timed_out=False,
                )
            )
        except Exception:
            pass
    finally:
        conn.close()


def execute_strategy(
    source_code: str,
    ctx: Any,
    *,
    timeout_seconds: float | None = None,
) -> StrategyExecutionResult:
    """Run strategy code in an isolated child process and return a structured result."""
    options = _runtime_options(timeout_seconds)
    started = time.perf_counter()
    proc_ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = proc_ctx.Pipe(duplex=False)
    process = proc_ctx.Process(
        target=_child_main,
        args=(child_conn, source_code, ctx, options),
        daemon=True,
    )
    process.start()
    child_conn.close()

    try:
        if parent_conn.poll(options.timeout_seconds):
            result = parent_conn.recv()
            if isinstance(result, StrategyExecutionResult):
                return result
            return StrategyExecutionResult(
                ok=False,
                error_type="StrategyRuntimeProtocolError",
                error_message="strategy child returned an invalid payload",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        process.terminate()
        process.join(timeout=0.2)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.2)
        return StrategyExecutionResult(
            ok=False,
            error_type="StrategyTimeoutError",
            error_message=f"strategy execution timed out after {options.timeout_seconds:g}s",
            traceback=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            timed_out=True,
        )
    except EOFError:
        return StrategyExecutionResult(
            ok=False,
            error_type="StrategyRuntimeError",
            error_message=f"strategy child exited without a result (exitcode={process.exitcode})",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        parent_conn.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.2)
