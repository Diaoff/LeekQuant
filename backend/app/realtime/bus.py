from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Protocol

import redis.asyncio as redis_async
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.realtime.models import RealtimeTick, realtime_channel


STREAM_MAXLEN = 1000
STREAM_FIELD_KEY = "msg"


class RealtimeUnavailable(RuntimeError):
    """Raised when the realtime Redis transport is unavailable."""


class RealtimeSubscription(Protocol):
    async def subscribe(self, ts_codes: set[str]) -> None: ...

    async def unsubscribe(self, ts_codes: set[str]) -> None: ...

    def listen(self) -> AsyncIterator[RealtimeTick]: ...

    async def close(self) -> None: ...


class RealtimeBus(Protocol):
    async def publish(self, tick: RealtimeTick) -> int: ...

    async def open_subscription(self, replay_from: str | None = None) -> RealtimeSubscription: ...


class RedisRealtimeSubscription:
    def __init__(
        self,
        pubsub: redis_async.client.PubSub,
        client: redis_async.Redis | None = None,
        *,
        replay_from: str | None = None,
        persistence: bool | None = None,
    ):
        self._pubsub = pubsub
        self._client = client
        self._replay_from = replay_from
        self._last_seen_id: str | None = None
        self._subscribed_channels: set[str] = set()
        self._persistence = (
            persistence if persistence is not None else get_settings().realtime_bus_persistence
        )

    async def subscribe(self, ts_codes: set[str]) -> None:
        if not ts_codes:
            return
        channels = [realtime_channel(code) for code in sorted(ts_codes)]
        try:
            await self._pubsub.subscribe(*channels)
            self._subscribed_channels.update(channels)
        except RedisError as exc:
            raise RealtimeUnavailable(f"realtime redis subscribe failed: {exc}") from exc

    async def unsubscribe(self, ts_codes: set[str]) -> None:
        if not ts_codes:
            return
        channels = [realtime_channel(code) for code in sorted(ts_codes)]
        try:
            await self._pubsub.unsubscribe(*channels)
            for channel in channels:
                self._subscribed_channels.discard(channel)
        except RedisError as exc:
            raise RealtimeUnavailable(f"realtime redis unsubscribe failed: {exc}") from exc

    async def _replay_history(self) -> AsyncIterator[RealtimeTick]:
        """Replay persisted stream entries from replay_from onwards.

        Used at listen() start when persistence is enabled and replay_from is set.
        replay_from can be:
        - "-" or None: from the beginning of the stream
        - "$": from the latest entry (effectively no history)
        - "<stream_id>": from after the given id (exclusive)
        """
        if not self._persistence or self._client is None or not self._subscribed_channels:
            return
        start_id = self._replay_from or "-"
        for channel in sorted(self._subscribed_channels):
            try:
                stream_entries = await self._client.xrange(channel, start_id, "+")
                for msg_id, fields in stream_entries:
                    # Skip the boundary id itself when replay_from is a real id
                    if start_id not in ("-", "$") and msg_id == start_id:
                        continue
                    data = fields.get(STREAM_FIELD_KEY)
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    if not data:
                        continue
                    try:
                        tick = RealtimeTick.from_payload(json.loads(data))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    self._last_seen_id = msg_id
                    yield tick
            except RedisError as exc:
                # Replay failures should not block real-time subscription.
                raise RealtimeUnavailable(
                    f"realtime redis stream replay failed for {channel}: {exc}"
                ) from exc

    async def listen(self) -> AsyncIterator[RealtimeTick]:
        # First replay history if persistence is enabled and replay_from is set
        if self._persistence and self._replay_from is not None:
            async for tick in self._replay_history():
                yield tick
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
        self._persistence = get_settings().realtime_bus_persistence

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
        channel = realtime_channel(tick.ts_code)
        payload = json.dumps(tick.to_payload(), ensure_ascii=False)
        try:
            count = int(await self.client.publish(channel, payload))
            if self._persistence:
                # Dual-write to a Redis Stream for replay capability.
                await self.client.xadd(channel, {STREAM_FIELD_KEY: payload})
                # Trim stream to a bounded size to cap memory usage (~1MB).
                await self.client.xtrim(channel, maxlen=STREAM_MAXLEN, approximate=True)
            return count
        except RedisError as exc:
            raise RealtimeUnavailable(f"realtime redis publish failed: {exc}") from exc

    async def open_subscription(self, replay_from: str | None = None) -> RedisRealtimeSubscription:
        try:
            client = redis_async.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            return RedisRealtimeSubscription(
                client.pubsub(),
                client,
                replay_from=replay_from,
                persistence=self._persistence,
            )
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
