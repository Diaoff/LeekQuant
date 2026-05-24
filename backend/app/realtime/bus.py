from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Protocol

import redis.asyncio as redis_async
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.realtime.models import RealtimeTick, realtime_channel


class RealtimeUnavailable(RuntimeError):
    """Raised when the realtime Redis transport is unavailable."""


class RealtimeSubscription(Protocol):
    async def subscribe(self, ts_codes: set[str]) -> None: ...

    async def unsubscribe(self, ts_codes: set[str]) -> None: ...

    def listen(self) -> AsyncIterator[RealtimeTick]: ...

    async def close(self) -> None: ...


class RealtimeBus(Protocol):
    async def publish(self, tick: RealtimeTick) -> int: ...

    async def open_subscription(self) -> RealtimeSubscription: ...


class RedisRealtimeSubscription:
    def __init__(self, pubsub: redis_async.client.PubSub, client: redis_async.Redis | None = None):
        self._pubsub = pubsub
        self._client = client

    async def subscribe(self, ts_codes: set[str]) -> None:
        if not ts_codes:
            return
        try:
            await self._pubsub.subscribe(*(realtime_channel(code) for code in sorted(ts_codes)))
        except RedisError as exc:
            raise RealtimeUnavailable(f"realtime redis subscribe failed: {exc}") from exc

    async def unsubscribe(self, ts_codes: set[str]) -> None:
        if not ts_codes:
            return
        try:
            await self._pubsub.unsubscribe(*(realtime_channel(code) for code in sorted(ts_codes)))
        except RedisError as exc:
            raise RealtimeUnavailable(f"realtime redis unsubscribe failed: {exc}") from exc

    async def listen(self) -> AsyncIterator[RealtimeTick]:
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                try:
                    yield RealtimeTick.from_payload(json.loads(data))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        except RedisError as exc:
            raise RealtimeUnavailable(f"realtime redis subscription failed: {exc}") from exc

    async def close(self) -> None:
        if hasattr(self._pubsub, "aclose"):
            await self._pubsub.aclose()
        else:
            await self._pubsub.close()
        if self._client is not None:
            if hasattr(self._client, "aclose"):
                await self._client.aclose()
            else:
                await self._client.close()


class RedisRealtimeBus:
    def __init__(self, redis_url: str | None = None):
        self.redis_url = redis_url or get_settings().redis_url
        self._client: redis_async.Redis | None = None

    @property
    def client(self) -> redis_async.Redis:
        if self._client is None:
            self._client = redis_async.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._client

    async def publish(self, tick: RealtimeTick) -> int:
        try:
            return int(await self.client.publish(realtime_channel(tick.ts_code), json.dumps(tick.to_payload(), ensure_ascii=False)))
        except RedisError as exc:
            raise RealtimeUnavailable(f"realtime redis publish failed: {exc}") from exc

    async def open_subscription(self) -> RedisRealtimeSubscription:
        try:
            client = redis_async.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            return RedisRealtimeSubscription(client.pubsub(), client)
        except RedisError as exc:
            raise RealtimeUnavailable(f"realtime redis subscription failed: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None:
            if hasattr(self._client, "aclose"):
                await self._client.aclose()
            else:
                await self._client.close()
            self._client = None


_realtime_bus: RedisRealtimeBus | None = None


def get_realtime_bus() -> RedisRealtimeBus:
    global _realtime_bus
    if _realtime_bus is None:
        _realtime_bus = RedisRealtimeBus()
    return _realtime_bus
