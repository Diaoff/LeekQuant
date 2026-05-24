from __future__ import annotations

import asyncio

import pytest
import redis
from fastapi.testclient import TestClient

from app.api.realtime import realtime_bus_dependency
from app.core.config import get_settings
from app.main import app
from app.realtime.bus import RedisRealtimeBus
from app.realtime.models import RealtimeTick


@pytest.mark.asyncio
async def test_realtime_redis_pubsub_round_trip_when_redis_is_available() -> None:
    settings = get_settings()
    try:
        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
    except Exception as exc:
        pytest.skip(f"Redis is not reachable for realtime integration: {exc}")

    bus = RedisRealtimeBus(settings.redis_url)
    subscription = await bus.open_subscription()
    try:
        await subscription.subscribe({"900001.SZ"})
        await bus.publish(RealtimeTick(ts_code="900001.SZ", price="10.3", change="0.1", change_pct="0.98"))
        tick = await asyncio.wait_for(anext(subscription.listen()), timeout=3)
    finally:
        await subscription.close()
        await bus.close()

    assert tick.ts_code == "900001.SZ"
    assert tick.to_payload()["price"] == "10.3"


def _skip_when_redis_unavailable() -> str:
    settings = get_settings()
    try:
        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
    except Exception as exc:
        pytest.skip(f"Redis is not reachable for realtime integration: {exc}")
    return settings.redis_url


def _publish_with_new_bus(redis_url: str, tick: RealtimeTick) -> int:
    async def publish() -> int:
        bus = RedisRealtimeBus(redis_url)
        try:
            return await bus.publish(tick)
        finally:
            await bus.close()

    return asyncio.run(publish())


def test_realtime_websocket_receives_redis_published_ticks_when_redis_is_available() -> None:
    redis_url = _skip_when_redis_unavailable()
    websocket_bus = RedisRealtimeBus(redis_url)

    async def override_bus() -> RedisRealtimeBus:
        return websocket_bus

    app.dependency_overrides[realtime_bus_dependency] = override_bus
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/realtime") as websocket:
                websocket.send_json({"action": "subscribe", "ts_codes": ["900001.SZ", "900002.SZ"]})
                assert websocket.receive_json() == {"type": "subscribed", "ts_codes": ["900001.SZ", "900002.SZ"]}

                _publish_with_new_bus(redis_url, RealtimeTick(ts_code="900003.SZ", price="8.8"))
                assert _publish_with_new_bus(redis_url, RealtimeTick(ts_code="900002.SZ", price="11.4")) >= 1
                tick = websocket.receive_json()
                assert tick["ts_code"] == "900002.SZ"
                assert tick["price"] == "11.4"

                websocket.send_json({"action": "unsubscribe", "ts_codes": ["900001.SZ"]})
                assert websocket.receive_json() == {"type": "subscribed", "ts_codes": ["900002.SZ"]}

                _publish_with_new_bus(redis_url, RealtimeTick(ts_code="900001.SZ", price="10.1"))
                assert _publish_with_new_bus(redis_url, RealtimeTick(ts_code="900002.SZ", price="11.5")) >= 1
                tick = websocket.receive_json()
                assert tick["ts_code"] == "900002.SZ"
                assert tick["price"] == "11.5"
    finally:
        app.dependency_overrides.clear()
        asyncio.run(websocket_bus.close())
