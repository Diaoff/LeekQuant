"""Tests for P1-7 (slow consumer backpressure) and P1-9 (application heartbeat)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from app.api import realtime as realtime_api
from app.api.realtime import (
    _heartbeat_loop,
    _pump_ticks,
    _safe_send_json,
    realtime_bus_dependency,
)
from app.main import app
from app.realtime.bus import RealtimeUnavailable
from app.realtime.models import RealtimeTick
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


class PauseableWebSocket:
    """WebSocket stub whose send_json can be programmatically delayed."""

    def __init__(self, *, send_delay: float = 0.0, fail_after_n_sends: int | None = None):
        self.sent: list[dict[str, Any]] = []
        self.send_delay = send_delay
        self.closed = False
        self.close_code: int | None = None
        self._fail_after = fail_after_n_sends
        self._send_count = 0
        self._lock = asyncio.Lock()

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._send_count += 1
            if self._fail_after is not None and self._send_count > self._fail_after:
                raise RuntimeError("send failed")
            if self.send_delay > 0:
                await asyncio.sleep(self.send_delay)
            self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code


class ScriptedSubscription:
    """Subscription stub that yields scripted ticks."""

    def __init__(self, ticks: list[RealtimeTick]) -> None:
        self._ticks = ticks
        self.closed = False
        self.subscribed: set[str] = set()

    async def subscribe(self, ts_codes: set[str]) -> None:
        self.subscribed.update(ts_codes)

    async def unsubscribe(self, ts_codes: set[str]) -> None:
        self.subscribed.difference_update(ts_codes)

    async def listen(self) -> AsyncIterator[RealtimeTick]:
        for tick in self._ticks:
            await asyncio.sleep(0)  # yield control
            yield tick
        # Block forever to keep pump running
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


# ============================================================
# P1-7: Slow consumer backpressure
# ============================================================


@pytest.mark.asyncio
async def test_slow_consumer_triggers_send_timeout_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Consumer should close the connection when send_json exceeds timeout."""
    # Speed up: timeout=0.1s, send delay=0.5s
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_send_timeout_seconds": 0.1,
                "ws_ping_interval_seconds": 0,  # disable heartbeat
                "ws_ping_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    websocket = PauseableWebSocket(send_delay=0.5)
    send_lock = asyncio.Lock()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    dropped_counter = {"count": 0}
    ticks = [RealtimeTick(ts_code="900001.SZ", price="10.0")]
    subscription = ScriptedSubscription(ticks)
    subscribed = {"900001.SZ"}

    pump_task = asyncio.create_task(
        _pump_ticks(websocket, subscription, subscribed, send_lock, queue, dropped_counter)
    )
    # Wait long enough for the send timeout to trigger (0.1s) + close
    await asyncio.sleep(0.5)
    # Producer may still be running (ScriptedSubscription.listen blocks forever);
    # cancel it to unblock gather
    pump_task.cancel()
    try:
        await pump_task
    except asyncio.CancelledError:
        pass

    # Connection should have been closed due to timeout
    assert websocket.closed is True
    assert websocket.close_code == 1011


@pytest.mark.asyncio
async def test_queue_full_drops_ticks_and_increments_counter(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When queue is full, ticks should be dropped and counter incremented."""
    # Disable send timeout (use very large value)
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_send_timeout_seconds": 5.0,
                "ws_ping_interval_seconds": 0,
                "ws_ping_timeout_seconds": 5.0,
                "ws_queue_maxsize": 2,
            },
        )(),
    )

    websocket = PauseableWebSocket(send_delay=0.3)  # slow consumer
    send_lock = asyncio.Lock()
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    dropped_counter = {"count": 0}
    # 5 ticks to push through a 2-capacity queue with slow consumer
    ticks = [
        RealtimeTick(ts_code="900001.SZ", price=str(i)) for i in range(5)
    ]
    subscription = ScriptedSubscription(ticks)
    subscribed = {"900001.SZ"}

    pump_task = asyncio.create_task(
        _pump_ticks(websocket, subscription, subscribed, send_lock, queue, dropped_counter)
    )
    # Give producer time to push all 5 ticks; only 2 fit in queue, 3 should drop
    await asyncio.sleep(0.1)
    # Stop the pump
    pump_task.cancel()
    try:
        await pump_task
    except asyncio.CancelledError:
        pass

    assert dropped_counter["count"] >= 1
    # Should have logged warnings about drops
    drop_logs = [r for r in caplog.records if "ws queue full" in r.message]
    assert len(drop_logs) >= 1


@pytest.mark.asyncio
async def test_normal_consumer_no_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Normal consumer should have zero drops."""
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_send_timeout_seconds": 5.0,
                "ws_ping_interval_seconds": 0,
                "ws_ping_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    websocket = PauseableWebSocket(send_delay=0)
    send_lock = asyncio.Lock()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    dropped_counter = {"count": 0}
    ticks = [RealtimeTick(ts_code="900001.SZ", price="10.0")]
    subscription = ScriptedSubscription(ticks)
    subscribed = {"900001.SZ"}

    pump_task = asyncio.create_task(
        _pump_ticks(websocket, subscription, subscribed, send_lock, queue, dropped_counter)
    )
    # Let consumer send the tick
    await asyncio.sleep(0.1)
    pump_task.cancel()
    try:
        await pump_task
    except asyncio.CancelledError:
        pass

    assert dropped_counter["count"] == 0
    assert len(websocket.sent) >= 1
    assert websocket.sent[0]["ts_code"] == "900001.SZ"


# ============================================================
# P1-9: Application heartbeat
# ============================================================


@pytest.mark.asyncio
async def test_heartbeat_sends_ping_periodically(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heartbeat loop should send ping frames at the configured interval."""
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_ping_interval_seconds": 0.05,
                "ws_ping_timeout_seconds": 5.0,
                "ws_send_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    websocket = PauseableWebSocket(send_delay=0)
    send_lock = asyncio.Lock()
    stop_event = asyncio.Event()

    task = asyncio.create_task(_heartbeat_loop(websocket, send_lock, stop_event))
    # Allow at least 2 ping intervals
    await asyncio.sleep(0.15)
    stop_event.set()
    try:
        await task
    except asyncio.CancelledError:
        pass

    pings = [p for p in websocket.sent if p.get("type") == "ping"]
    assert len(pings) >= 2, f"expected >= 2 pings, got {len(pings)}"
    # Each ping should have a ts field
    assert all("ts" in p for p in pings)


@pytest.mark.asyncio
async def test_heartbeat_closes_connection_on_ping_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heartbeat should close the connection when ping send times out."""
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_ping_interval_seconds": 0.05,
                "ws_ping_timeout_seconds": 0.02,  # shorter than send_delay
                "ws_send_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    websocket = PauseableWebSocket(send_delay=0.1)  # slower than timeout
    send_lock = asyncio.Lock()
    stop_event = asyncio.Event()

    task = asyncio.create_task(_heartbeat_loop(websocket, send_lock, stop_event))
    # Wait for one heartbeat cycle to time out
    await asyncio.sleep(0.3)
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass

    assert websocket.closed is True
    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_heartbeat_disabled_when_interval_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """When WS_PING_INTERVAL_SECONDS=0, heartbeat should not send any pings."""
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_ping_interval_seconds": 0,
                "ws_ping_timeout_seconds": 5.0,
                "ws_send_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    websocket = PauseableWebSocket(send_delay=0)
    send_lock = asyncio.Lock()
    stop_event = asyncio.Event()

    # Should return immediately
    await _heartbeat_loop(websocket, send_lock, stop_event)

    await asyncio.sleep(0.05)
    # No pings should have been sent
    pings = [p for p in websocket.sent if p.get("type") == "ping"]
    assert pings == []
    assert websocket.closed is False


@pytest.mark.asyncio
async def test_heartbeat_stops_when_stop_event_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heartbeat loop should exit when stop_event is set."""
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_ping_interval_seconds": 0.05,
                "ws_ping_timeout_seconds": 5.0,
                "ws_send_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    websocket = PauseableWebSocket(send_delay=0)
    send_lock = asyncio.Lock()
    stop_event = asyncio.Event()

    task = asyncio.create_task(_heartbeat_loop(websocket, send_lock, stop_event))
    await asyncio.sleep(0.02)  # let it start sleeping
    stop_event.set()
    # Should exit promptly
    await asyncio.wait_for(task, timeout=1.0)
    assert websocket.closed is False


# ============================================================
# C-3: pump_ticks sentinel — consumer must exit when producer ends
# ============================================================


class FiniteSubscription:
    """Subscription whose listen() naturally terminates after yielding all ticks."""

    def __init__(self, ticks: list[RealtimeTick]) -> None:
        self._ticks = ticks
        self.closed = False
        self.subscribed: set[str] = set()

    async def subscribe(self, ts_codes: set[str]) -> None:
        self.subscribed.update(ts_codes)

    async def unsubscribe(self, ts_codes: set[str]) -> None:
        self.subscribed.difference_update(ts_codes)

    async def listen(self) -> AsyncIterator[RealtimeTick]:
        for tick in self._ticks:
            await asyncio.sleep(0)
            yield tick
        # Natural termination — no blocking wait

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_pump_ticks_consumer_exits_on_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    """When producer's listen() ends naturally, consumer must exit promptly via sentinel.

    Regression for C-3: previously consumer blocked forever on queue.get() after
    producer returned, leaking the asyncio task.
    """
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_send_timeout_seconds": 5.0,
                "ws_ping_interval_seconds": 0,
                "ws_ping_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    websocket = PauseableWebSocket(send_delay=0)
    send_lock = asyncio.Lock()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    dropped_counter = {"count": 0}
    ticks = [RealtimeTick(ts_code="900001.SZ", price="10.0")]
    subscription = FiniteSubscription(ticks)
    subscribed = {"900001.SZ"}

    # Run _pump_ticks to completion — should not hang
    await asyncio.wait_for(
        _pump_ticks(websocket, subscription, subscribed, send_lock, queue, dropped_counter),
        timeout=2.0,
    )

    # Consumer should have processed the tick and exited via sentinel
    assert len(websocket.sent) == 1
    assert websocket.sent[0]["ts_code"] == "900001.SZ"
    assert websocket.closed is False  # clean exit, no close needed


# ============================================================
# P1-12: replay_from query param passed to bus.open_subscription
# ============================================================


class _CaptureBus:
    """Minimal RealtimeBus stub that captures the replay_from argument
    passed to open_subscription, then returns a FiniteSubscription."""

    def __init__(self, ticks: list[RealtimeTick]):
        self._ticks = ticks
        self.open_subscription_calls: list[str | None] = []

    async def open_subscription(self, replay_from: str | None = None):
        self.open_subscription_calls.append(replay_from)
        return FiniteSubscription(self._ticks)


@pytest.mark.asyncio
async def test_ws_replay_from_query_param_passed_to_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /ws/realtime?replay_from=<id>`` must forward the id to
    ``bus.open_subscription(replay_from=...)`` so the client can recover
    ticks missed during a disconnect window. P1-12 regression."""
    from app.api import realtime as realtime_api
    from app.realtime.models import RealtimeTick

    class _AcceptThenCloseWebSocket:
        """WebSocket stub: accepts, then immediately raises WebSocketDisconnect
        on the next receive_json() to let the handler exit its loop cleanly."""

        def __init__(self):
            self.accepted = False
            self.closed = False
            self.close_code: int | None = None

        async def accept(self):
            self.accepted = True

        async def close(self, code: int = 1000):
            self.closed = True
            self.close_code = code

        async def receive_json(self):
            # Simulate a client disconnect on the first receive — handler
            # will catch WebSocketDisconnect and break out of its loop,
            # letting the finally block run subscription.close() cleanly.
            await asyncio.sleep(0)  # yield once so handler sets up tasks
            raise WebSocketDisconnect(code=1000)

    bus = _CaptureBus(ticks=[RealtimeTick(ts_code="900001.SZ", price="10.0")])
    ws = _AcceptThenCloseWebSocket()

    async def fake_bus_dependency():
        return bus

    monkeypatch.setattr(realtime_api, "realtime_bus_dependency", fake_bus_dependency)

    # Disable heartbeat to avoid extra background tasks that would complicate
    # the test lifecycle.
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_ping_interval_seconds": 0,
                "ws_ping_timeout_seconds": 5.0,
                "ws_send_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    # Invoke the endpoint directly (bypassing FastAPI's ASGI plumbing) so we
    # can verify the replay_from parameter is forwarded to the bus.
    handler_task = asyncio.create_task(
        realtime_api.realtime_websocket(websocket=ws, replay_from="1690000000000-0", bus=bus)
    )
    # Give the handler a moment to call open_subscription + enter its receive
    # loop, then cancel it to unblock the receive_json() future.
    await asyncio.sleep(0.05)
    handler_task.cancel()
    try:
        await handler_task
    except asyncio.CancelledError:
        pass

    assert ws.accepted is True
    assert bus.open_subscription_calls == ["1690000000000-0"]


@pytest.mark.asyncio
async def test_ws_replay_from_defaults_to_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``replay_from`` query param is absent, the bus must receive ``None``
    (equivalent to "give me everything" — backwards-compatible with pre-P1-12
    clients)."""
    from app.api import realtime as realtime_api
    from app.realtime.models import RealtimeTick

    class _AcceptThenBlockWebSocket:
        async def accept(self):
            return None

        async def close(self, code: int = 1000):
            return None

        async def receive_json(self):
            await asyncio.sleep(0)
            raise WebSocketDisconnect(code=1000)

    bus = _CaptureBus(ticks=[RealtimeTick(ts_code="900001.SZ", price="10.0")])
    ws = _AcceptThenBlockWebSocket()

    async def fake_bus_dependency():
        return bus

    monkeypatch.setattr(realtime_api, "realtime_bus_dependency", fake_bus_dependency)
    monkeypatch.setattr(
        "app.api.realtime.get_settings",
        lambda: type(
            "S",
            (),
            {
                "ws_ping_interval_seconds": 0,
                "ws_ping_timeout_seconds": 5.0,
                "ws_send_timeout_seconds": 5.0,
                "ws_queue_maxsize": 100,
            },
        )(),
    )

    handler_task = asyncio.create_task(
        realtime_api.realtime_websocket(websocket=ws, replay_from=None, bus=bus)
    )
    await asyncio.sleep(0.05)
    handler_task.cancel()
    try:
        await handler_task
    except asyncio.CancelledError:
        pass

    assert bus.open_subscription_calls == [None]

