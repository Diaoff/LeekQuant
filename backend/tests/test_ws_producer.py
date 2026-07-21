"""Tests for C-4: ws_producer reconnect and client.close() cleanup.

Regression for resource leak where stream_task.cancel() was not awaited and
client.close() was never called, leaking file descriptors and the WS connection.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

import pytest

from app.realtime import ws_producer
from app.realtime.ws_producer import run_dynamic_ws_producer


class FakeClient:
    """Fake EastMoneyWSClient that tracks close() invocations."""

    def __init__(self, ticks: list[Any] | None = None, raise_on_stream: Exception | None = None):
        self._ticks = ticks or []
        self._raise = raise_on_stream
        self.closed_count = 0
        self.stream_started = 0

    async def stream(self):
        self.stream_started += 1
        if self._raise is not None:
            raise self._raise
        for tick in self._ticks:
            await asyncio.sleep(0)
            yield tick

    async def close(self) -> None:
        self.closed_count += 1


class FakeBus:
    """Fake RealtimeBus that records published ticks."""

    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, tick: Any) -> int:
        self.published.append(tick)
        return 1


def _dummy_session_factory():
    """Dummy session_factory that does nothing; tests patch _load_dynamic_codes."""
    class _Ctx:
        async def __aenter__(self):
            return None
        async def __aexit__(self, *args):
            return None
    return _Ctx()


@pytest.mark.asyncio
async def test_stream_task_cancelled_awaits_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """When stream_task completes naturally (or reload wins), the pending sibling
    must be cancelled+awaited and client.close() must be invoked.

    Regression for C-4: previously stream_task.cancel() was not awaited, so
    client.close() never ran, leaking the WS file descriptor.
    """
    fake_client = FakeClient(ticks=[])
    fake_bus = FakeBus()

    # Patch EastMoneyWSClient constructor to return our fake
    monkeypatch.setattr(
        ws_producer, "EastMoneyWSClient", lambda codes: fake_client
    )

    # Patch _load_dynamic_codes to return fixed codes (avoids DB)
    async def fake_load(_factory):
        return ["600000.SH"]

    monkeypatch.setattr(ws_producer, "_load_dynamic_codes", fake_load)

    # Short reload interval so each iteration completes quickly
    reload_interval = 0.02

    # Pass dummy session_factory to avoid lazy import of app.db.session
    # (which triggers create_async_engine inside the running loop and
    # corrupts asyncio.Event/Lock state).
    task = asyncio.create_task(
        run_dynamic_ws_producer(
            reload_interval=reload_interval,
            bus=fake_bus,
            session_factory=_dummy_session_factory,
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # client.close() must have been called at least once per iteration
    assert fake_client.closed_count >= 1, (
        f"client.close() never called (closed_count={fake_client.closed_count})"
    )


@pytest.mark.asyncio
async def test_stream_failure_triggers_immediate_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """When stream_task raises an exception (not cancelled), the producer must
    call client.close() during cleanup and reconnect after exponential backoff
    rather than waiting for reload_interval.
    """
    failing_client = FakeClient(raise_on_stream=RuntimeError("WS connect failed"))
    success_client = FakeClient(ticks=[])
    clients = [failing_client, success_client]
    call_idx = {"i": 0}

    def make_client(codes):
        c = clients[min(call_idx["i"], len(clients) - 1)]
        call_idx["i"] += 1
        return c

    monkeypatch.setattr(ws_producer, "EastMoneyWSClient", make_client)

    async def fake_load(_factory):
        return ["600000.SH"]

    monkeypatch.setattr(ws_producer, "_load_dynamic_codes", fake_load)

    fake_bus = FakeBus()
    # Large reload_interval so reload_task doesn't win — only stream failure
    # triggers the reconnect path.
    reload_interval = 100.0

    task = asyncio.create_task(
        run_dynamic_ws_producer(
            reload_interval=reload_interval,
            bus=fake_bus,
            session_factory=_dummy_session_factory,
        )
    )

    # Wait long enough for the failing stream + 1s backoff (2^0=1s) + retry
    await asyncio.sleep(1.2)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # Failing client.close() must have been called during cleanup
    assert failing_client.closed_count >= 1, (
        f"failing_client.close() never called (closed_count={failing_client.closed_count})"
    )
    # We should have created at least 2 clients (1 failing + 1 retry)
    assert call_idx["i"] >= 2, (
        f"expected immediate retry after backoff, got {call_idx['i']} client creations"
    )
