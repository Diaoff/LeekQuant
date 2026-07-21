from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import realtime as realtime_api
from app.api.realtime import _safe_send_json, realtime_bus_dependency
from app.db.session import get_session
from app.main import app
from app.realtime.bus import RedisRealtimeBus, RealtimeUnavailable
from app.realtime.models import RealtimeTick, realtime_channel
from app.realtime.providers import EastMoneyRealtimeProvider
from app.realtime.risk_guard import get_risk_guard_status


def test_realtime_tick_serializes_standard_fields() -> None:
    tick = RealtimeTick(
        ts_code="000001.sz",
        price=Decimal("12.340"),
        change=Decimal("0.120"),
        change_pct=Decimal("0.9821"),
        volume=1000,
        amount=Decimal("12340.00"),
        bid1=Decimal("12.33"),
        ask1=Decimal("12.35"),
        ts=datetime(2026, 5, 24, 9, 30, tzinfo=timezone.utc),
    )

    assert realtime_channel("000001.sz") == "realtime:000001.SZ"
    assert tick.to_payload() == {
        "ts_code": "000001.SZ",
        "price": "12.340",
        "change": "0.120",
        "change_pct": "0.9821",
        "volume": 1000,
        "amount": "12340.00",
        "bid1": "12.33",
        "ask1": "12.35",
        "ts": "2026-05-24T09:30:00+00:00",
    }


class FakeResult:
    def __init__(self, rows=None, rowcount=1):
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, statement, params=None):
        return FakeResult(self.rows)


@pytest.mark.asyncio
async def test_risk_guard_status_missing_when_no_heartbeat() -> None:
    status = await get_risk_guard_status(FakeSession([]))  # type: ignore[arg-type]

    assert status["status"] == "missing"
    assert status["last_seen_at"] is None
    assert status["loaded_positions"] == 0


@pytest.mark.asyncio
async def test_risk_guard_status_running_from_recent_heartbeat() -> None:
    seen_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    status = await get_risk_guard_status(
        FakeSession(
            [
                {
                    "started_at": seen_at,
                    "payload": {"refresh_interval_seconds": 30},
                    "result": {
                        "last_seen_at": seen_at.isoformat(),
                        "refresh_interval_seconds": 30,
                        "loaded_positions": 2,
                        "tracked_symbols": 1,
                        "last_error": None,
                        "last_trigger": {"ts_code": "000001.SZ", "reason": "止盈"},
                        "last_blocked_reason": None,
                    },
                    "error_message": None,
                }
            ]
        )  # type: ignore[arg-type]
    )

    assert status["status"] == "running"
    assert status["loaded_positions"] == 2
    assert status["tracked_symbols"] == 1
    assert status["last_trigger"]["ts_code"] == "000001.SZ"


@pytest.mark.asyncio
async def test_risk_guard_status_stale_after_interval_grace() -> None:
    seen_at = datetime.now(timezone.utc) - timedelta(seconds=130)
    status = await get_risk_guard_status(
        FakeSession(
            [
                {
                    "started_at": seen_at,
                    "payload": {"refresh_interval_seconds": 30},
                    "result": {"last_seen_at": seen_at.isoformat(), "refresh_interval_seconds": 30},
                    "error_message": "snapshot down",
                }
            ]
        )  # type: ignore[arg-type]
    )

    assert status["status"] == "stale"
    assert status["last_error"] == "snapshot down"


@pytest.mark.asyncio
async def test_redis_bus_publishes_tick_to_code_channel() -> None:
    calls: list[tuple[str, str]] = []

    class FakeRedis:
        async def publish(self, channel: str, payload: str) -> int:
            calls.append((channel, payload))
            return 2

        async def xadd(self, *_args, **_kwargs) -> str:
            return "0-0"

        async def xtrim(self, *_args, **_kwargs) -> int:
            return 0

    bus = RedisRealtimeBus(redis_url="redis://test")
    bus._client = FakeRedis()  # type: ignore[assignment]

    count = await bus.publish(RealtimeTick(ts_code="900001.SZ", price="10.1"))

    assert count == 2
    assert calls[0][0] == "realtime:900001.SZ"
    assert json.loads(calls[0][1])["price"] == "10.1"


@pytest.mark.asyncio
async def test_redis_subscription_ignores_bad_messages_and_yields_ticks() -> None:
    class FakePubSub:
        async def listen(self) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "subscribe", "data": 1}
            yield {"type": "message", "data": "{bad json"}
            yield {"type": "message", "data": json.dumps(RealtimeTick(ts_code="900001.SZ", price="10.1").to_payload())}

        async def close(self) -> None:
            return None

    from app.realtime.bus import RedisRealtimeSubscription

    subscription = RedisRealtimeSubscription(FakePubSub())  # type: ignore[arg-type]
    tick = await anext(subscription.listen())

    assert tick.ts_code == "900001.SZ"
    assert tick.price == Decimal("10.1")


@pytest.mark.asyncio
async def test_redis_subscription_close_closes_pubsub_and_dedicated_client() -> None:
    class FakePubSub:
        def __init__(self) -> None:
            self.closed = False

        async def listen(self) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "subscribe", "data": 1}

        async def close(self) -> None:
            self.closed = True

    class FakeClient:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    from app.realtime.bus import RedisRealtimeSubscription

    pubsub = FakePubSub()
    client = FakeClient()
    subscription = RedisRealtimeSubscription(pubsub, client)  # type: ignore[arg-type]

    await subscription.close()

    assert pubsub.closed is True
    assert client.closed is True


class FakeSubscription:
    def __init__(self) -> None:
        self.subscribed: set[str] = set()
        self.closed = False
        self.scripted_ticks: list[RealtimeTick] = []

    async def subscribe(self, ts_codes: set[str]) -> None:
        self.subscribed.update(ts_codes)

    async def unsubscribe(self, ts_codes: set[str]) -> None:
        self.subscribed.difference_update(ts_codes)

    async def listen(self) -> AsyncIterator[RealtimeTick]:
        while not self.subscribed:
            await asyncio.sleep(0.001)
        for tick in self.scripted_ticks:
            await asyncio.sleep(0.01)
            yield tick
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


class FakeBus:
    def __init__(self) -> None:
        self.subscription = FakeSubscription()

    async def publish(self, tick: RealtimeTick) -> int:
        self.subscription.scripted_ticks.append(tick)
        return 1

    async def open_subscription(self, replay_from: str | None = None) -> FakeSubscription:
        return self.subscription


def test_realtime_websocket_subscribe_unsubscribe_and_filter() -> None:
    fake_bus = FakeBus()

    async def override_bus() -> FakeBus:
        return fake_bus

    app.dependency_overrides[realtime_bus_dependency] = override_bus
    try:
        fake_bus.subscription.scripted_ticks.extend(
            [
                RealtimeTick(ts_code="900003.SZ", price="8.8"),
                RealtimeTick(ts_code="900001.SZ", price="10.2"),
            ]
        )
        with TestClient(app) as client:
            with client.websocket_connect("/ws/realtime") as websocket:
                websocket.send_json({"action": "subscribe", "ts_codes": ["900001.SZ", "900002.SZ"]})
                assert websocket.receive_json() == {"type": "subscribed", "ts_codes": ["900001.SZ", "900002.SZ"]}
                assert fake_bus.subscription.subscribed == {"900001.SZ", "900002.SZ"}

                received = websocket.receive_json()
                assert received["ts_code"] == "900001.SZ"
                assert received["price"] == "10.2"

                websocket.send_json({"action": "unsubscribe", "ts_codes": ["900001.SZ"]})
                assert websocket.receive_json() == {"type": "subscribed", "ts_codes": ["900002.SZ"]}
                assert fake_bus.subscription.subscribed == {"900002.SZ"}
    finally:
        app.dependency_overrides.clear()

    assert fake_bus.subscription.closed is True
    assert get_session not in app.dependency_overrides


def test_realtime_websocket_reports_invalid_messages() -> None:
    fake_bus = FakeBus()

    async def override_bus() -> FakeBus:
        return fake_bus

    app.dependency_overrides[realtime_bus_dependency] = override_bus
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/realtime") as websocket:
                websocket.send_json({"action": "subscribe", "ts_codes": []})
                response = websocket.receive_json()
                assert response["type"] == "error"
                assert "must not be empty" in response["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_realtime_safe_send_treats_disconnected_client_as_closed() -> None:
    class ClosedWebSocket:
        async def send_json(self, _payload: dict[str, Any]) -> None:
            from starlette.websockets import WebSocketDisconnect

            raise WebSocketDisconnect(code=1006)

    assert await _safe_send_json(ClosedWebSocket(), {"type": "subscribed"}) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_realtime_safe_send_serializes_concurrent_sends() -> None:
    class ConcurrentDetectingWebSocket:
        def __init__(self) -> None:
            self.active_sends = 0
            self.max_active_sends = 0
            self.payloads: list[dict[str, Any]] = []

        async def send_json(self, payload: dict[str, Any]) -> None:
            self.active_sends += 1
            self.max_active_sends = max(self.max_active_sends, self.active_sends)
            await asyncio.sleep(0.01)
            self.payloads.append(payload)
            self.active_sends -= 1

    websocket = ConcurrentDetectingWebSocket()
    send_lock = asyncio.Lock()

    results = await asyncio.gather(
        _safe_send_json(websocket, {"type": "first"}, send_lock),  # type: ignore[arg-type]
        _safe_send_json(websocket, {"type": "second"}, send_lock),  # type: ignore[arg-type]
    )

    assert results == [True, True]
    assert websocket.max_active_sends == 1
    assert {payload["type"] for payload in websocket.payloads} == {"first", "second"}


@pytest.mark.asyncio
async def test_safe_send_json_swallows_connection_reset_error() -> None:
    """ConnectionResetError should not propagate; return False to caller."""
    class ResetWebSocket:
        async def send_json(self, _payload: dict[str, Any]) -> None:
            raise ConnectionResetError("peer reset")

    assert await _safe_send_json(ResetWebSocket(), {"type": "tick"}) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_safe_send_json_swallows_broken_pipe_error() -> None:
    """BrokenPipeError should not propagate; return False to caller."""
    class BrokenPipeWebSocket:
        async def send_json(self, _payload: dict[str, Any]) -> None:
            raise BrokenPipeError("broken pipe")

    assert await _safe_send_json(BrokenPipeWebSocket(), {"type": "tick"}) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_safe_send_json_reraises_cancelled_error() -> None:
    """asyncio.CancelledError must propagate, not be swallowed."""
    class CancelledWebSocket:
        async def send_json(self, _payload: dict[str, Any]) -> None:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _safe_send_json(CancelledWebSocket(), {"type": "tick"})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_safe_send_json_swallows_unknown_exception_with_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown exceptions should be caught, logged, and return False (not propagate)."""
    class WeirdErrorWebSocket:
        async def send_json(self, _payload: dict[str, Any]) -> None:
            raise AttributeError("missing attribute")

    with caplog.at_level("ERROR", logger="app.api.realtime"):
        result = await _safe_send_json(WeirdErrorWebSocket(), {"type": "tick"})  # type: ignore[arg-type]

    assert result is False
    assert any(
        "unexpected websocket send failure" in record.message for record in caplog.records
    ), "expected logger.exception call for unknown send failure"


def test_realtime_websocket_closes_subscription_when_client_disconnects() -> None:
    fake_bus = FakeBus()

    async def override_bus() -> FakeBus:
        return fake_bus

    app.dependency_overrides[realtime_bus_dependency] = override_bus
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/realtime") as websocket:
                websocket.send_json({"action": "subscribe", "ts_codes": ["900001.SZ"]})
                assert websocket.receive_json() == {"type": "subscribed", "ts_codes": ["900001.SZ"]}
                websocket.close()
    finally:
        app.dependency_overrides.clear()

    assert fake_bus.subscription.closed is True


def test_realtime_websocket_reports_redis_unavailable() -> None:
    class DownBus:
        async def publish(self, tick: RealtimeTick) -> int:
            return 0

        async def open_subscription(self, replay_from: str | None = None) -> FakeSubscription:
            raise RealtimeUnavailable("redis down")

    async def override_bus() -> DownBus:
        return DownBus()

    app.dependency_overrides[realtime_bus_dependency] = override_bus
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/realtime") as websocket:
                response = websocket.receive_json()
                assert response == {"type": "error", "detail": "redis down"}
    finally:
        app.dependency_overrides.clear()


def test_realtime_websocket_reports_pump_failure_and_closes_subscription() -> None:
    class FailingSubscription(FakeSubscription):
        async def listen(self) -> AsyncIterator[RealtimeTick]:
            while not self.subscribed:
                await asyncio.sleep(0.001)
            raise RealtimeUnavailable("redis listener down")
            yield  # pragma: no cover

    class FailingBus(FakeBus):
        def __init__(self) -> None:
            self.subscription = FailingSubscription()

    fake_bus = FailingBus()

    async def override_bus() -> FailingBus:
        return fake_bus

    app.dependency_overrides[realtime_bus_dependency] = override_bus
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/realtime") as websocket:
                websocket.send_json({"action": "subscribe", "ts_codes": ["900001.SZ"]})
                assert websocket.receive_json() == {"type": "subscribed", "ts_codes": ["900001.SZ"]}
                response = websocket.receive_json()
                assert response["type"] == "error"
                assert "redis listener down" in response["detail"]
    finally:
        app.dependency_overrides.clear()

    assert fake_bus.subscription.closed is True


def test_realtime_snapshot_returns_provider_ticks(monkeypatch) -> None:
    class FakeProvider:
        def __init__(self, ts_codes: list[str]) -> None:
            self.ts_codes = ts_codes

        async def fetch_snapshot(self) -> list[RealtimeTick]:
            return [RealtimeTick(ts_code=self.ts_codes[0], price="10.55", change="0.25", change_pct="2.43")]

    monkeypatch.setattr(realtime_api, "realtime_provider_factory", FakeProvider)

    with TestClient(app) as client:
        response = client.get("/api/realtime/snapshot?ts_codes=900001.SZ")

    assert response.status_code == 200
    assert response.json()["items"][0]["price"] == "10.55"
    assert response.json()["errors"] == []


def test_realtime_snapshot_reports_provider_error(monkeypatch) -> None:
    from app.data.providers import DataProviderError

    class DownProvider:
        def __init__(self, _ts_codes: list[str]) -> None:
            return None

        async def fetch_snapshot(self) -> list[RealtimeTick]:
            raise DataProviderError("quote source down")

    monkeypatch.setattr(realtime_api, "realtime_provider_factory", DownProvider)

    with TestClient(app) as client:
        response = client.get("/api/realtime/snapshot?ts_codes=900001.SZ")

    assert response.status_code == 200
    assert response.json() == {"items": [], "errors": ["quote source down"]}


@pytest.mark.asyncio
async def test_eastmoney_snapshot_maps_quote_price_not_previous_close(monkeypatch) -> None:
    from app.realtime import providers

    requested_urls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "data": {
                    "diff": [
                        {
                            "f12": "000001",
                            "f13": 0,
                            "f2": 10.68,
                            "f3": -0.19,
                            "f4": -0.02,
                            "f5": 552123,
                            "f6": 589901234,
                            "f18": 10.70,
                            "f31": 10.67,
                            "f32": 10.68,
                        }
                    ]
                }
            }

    class FakeAsyncClient:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, url: str, params: dict[str, str]) -> FakeResponse:
            requested_urls.append(url)
            assert params["secids"] == "0.000001"
            assert "f2" in params["fields"]
            assert "f18" in params["fields"]
            return FakeResponse()

    monkeypatch.setattr(providers.httpx, "AsyncClient", FakeAsyncClient)

    ticks = await EastMoneyRealtimeProvider(["000001.SZ"]).fetch_snapshot()

    assert len(ticks) == 1
    tick = ticks[0]
    assert tick.ts_code == "000001.SZ"
    assert tick.price == Decimal("10.68")
    assert tick.change == Decimal("-0.02")
    assert tick.change_pct == Decimal("-0.19")
    assert tick.price != Decimal("10.70")
    assert requested_urls == [EastMoneyRealtimeProvider.SNAPSHOT_URLS[0]]


def test_ws_tasks_subscribe_and_receive_events() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tasks") as websocket:
            websocket.send_json({"action": "subscribe"})
            response = websocket.receive_json()
            assert response["type"] == "subscribed"
            assert response["channel"] == "celery:task_events"


def test_ws_tasks_unsubscribe() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tasks") as websocket:
            websocket.send_json({"action": "subscribe"})
            assert websocket.receive_json()["type"] == "subscribed"

            websocket.send_json({"action": "unsubscribe"})
            response = websocket.receive_json()
            assert response["type"] == "unsubscribed"
            assert response["channel"] == "celery:task_events"


def test_ws_tasks_reports_invalid_messages() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tasks") as websocket:
            websocket.send_json("not a dict")
            response = websocket.receive_json()
            assert response["type"] == "error"
            assert "JSON object" in response["detail"]


def test_ws_tasks_reports_unknown_action() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tasks") as websocket:
            websocket.send_json({"action": "unknown"})
            response = websocket.receive_json()
            assert response["type"] == "error"
            assert "subscribe or unsubscribe" in response["detail"]


def test_ws_signals_subscribe_and_receive_events() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signals") as websocket:
            websocket.send_json({"action": "subscribe"})
            response = websocket.receive_json()
            assert response["type"] == "subscribed"
            assert response["channel"] == "signal:new"


def test_ws_signals_unsubscribe() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signals") as websocket:
            websocket.send_json({"action": "subscribe"})
            assert websocket.receive_json()["type"] == "subscribed"

            websocket.send_json({"action": "unsubscribe"})
            response = websocket.receive_json()
            assert response["type"] == "unsubscribed"
            assert response["channel"] == "signal:new"


def test_ws_signals_reports_invalid_messages() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signals") as websocket:
            websocket.send_json("not a dict")
            response = websocket.receive_json()
            assert response["type"] == "error"
            assert "JSON object" in response["detail"]


def test_eastmoney_stream_method_delegates_to_ws_client(monkeypatch) -> None:
    from app.realtime import providers

    class FakeWSClient:
        def __init__(self, ts_codes):
            self.ts_codes = ts_codes

        async def stream(self):
            yield RealtimeTick(ts_code="000001.SZ", price="10.5")

        async def close(self):
            pass

    monkeypatch.setattr("app.realtime.eastmoney_ws.EastMoneyWSClient", FakeWSClient)

    async def run_test():
        provider = EastMoneyRealtimeProvider(["000001.SZ"])
        ticks = []
        async for tick in provider.stream():
            ticks.append(tick)
        return ticks

    ticks = asyncio.get_event_loop().run_until_complete(run_test())
    assert len(ticks) == 1
    assert ticks[0].ts_code == "000001.SZ"
