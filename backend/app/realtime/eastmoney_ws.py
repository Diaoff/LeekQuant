"""EastMoney WebSocket streaming protocol adapter.

Connects to the EastMoney push2ws endpoint, subscribes to stock codes,
and yields RealtimeTick objects via an async generator. Includes automatic
reconnection with exponential backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

import websockets
import websockets.exceptions

from app.realtime.models import RealtimeTick, normalize_ts_code
from app.realtime.providers import EastMoneyRealtimeProvider

logger = logging.getLogger(__name__)


class EastMoneyWSClient:
    """Streaming client for the EastMoney push WebSocket feed."""

    WS_URLS = (
        "wss://push2ws.eastmoney.com/",
        "wss://82.push2ws.eastmoney.com/",
        "wss://45.push2ws.eastmoney.com/",
    )

    RECONNECT_BASE_DELAY = 1.0
    RECONNECT_MAX_DELAY = 30.0
    RECONNECT_BACKOFF_FACTOR = 2.0

    FIELDS = "f1,f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18,f30,f31,f32"

    def __init__(self, ts_codes: list[str]):
        self.ts_codes = [normalize_ts_code(c) for c in ts_codes]
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._reconnect_delay = self.RECONNECT_BASE_DELAY

    @staticmethod
    def _secid(ts_code: str) -> str:
        """Convert ts_code to EastMoney secid format: market.symbol."""
        symbol, exchange = normalize_ts_code(ts_code).split(".", 1)
        market = "1" if exchange == "SH" else "0"
        return f"{market}.{symbol}"

    def _build_subscribe_message(self) -> str:
        """Build the subscription JSON payload for the EastMoney WS."""
        return json.dumps(
            {
                "uid": str(uuid.uuid4()),
                "deepest": 10,
                "fields": self.FIELDS,
                "secids": ",".join(self._secid(code) for code in self.ts_codes),
            }
        )

    def _parse_message(self, raw: str | bytes) -> list[RealtimeTick]:
        """Parse a WS text frame into RealtimeTick list. Returns empty list for non-data frames."""
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8", errors="ignore")
            except Exception:
                logger.debug("silent except in _parse_message")
                return []
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.debug("silent except in _parse_message")
            return []

        if not isinstance(msg, dict):
            return []

        msg_type = (msg.get("type") or msg.get("Type") or "").lower()
        if msg_type in ("ping", "pong", "heartbeat"):
            return []

        data = msg.get("data")
        if not isinstance(data, dict):
            return []

        rows = data.get("diff")
        if not isinstance(rows, list):
            return []

        codes_set = set(self.ts_codes)
        ticks: list[RealtimeTick] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            tick = EastMoneyRealtimeProvider._tick_from_row(row)
            if tick is not None and tick.ts_code in codes_set:
                ticks.append(tick)
        return ticks

    async def stream(self) -> AsyncIterator[RealtimeTick]:
        """Yield ticks with automatic reconnection and exponential backoff."""
        url_index = 0
        while True:
            url = self.WS_URLS[url_index % len(self.WS_URLS)]
            try:
                async with websockets.connect(
                    url,
                    additional_headers={"User-Agent": "Mozilla/5.0"},
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=2**20,
                ) as ws:
                    self._ws = ws
                    self._reconnect_delay = self.RECONNECT_BASE_DELAY
                    logger.info("Connected to EastMoney WS: %s", url)

                    await ws.send(self._build_subscribe_message())

                    async for raw in ws:
                        ticks = self._parse_message(raw)
                        for tick in ticks:
                            yield tick
            except (
                websockets.ConnectionClosed,
                websockets.exceptions.InvalidStatusCode,
                ConnectionRefusedError,
                OSError,
                asyncio.TimeoutError,
            ) as exc:
                logger.warning(
                    "EastMoney WS connection lost (%s): %s, reconnecting in %.1fs",
                    url,
                    exc,
                    self._reconnect_delay,
                )
            except Exception:
                logger.exception("EastMoney WS unexpected error, reconnecting in %.1fs", self._reconnect_delay)
            finally:
                self._ws = None
                url_index += 1

            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * self.RECONNECT_BACKOFF_FACTOR,
                self.RECONNECT_MAX_DELAY,
            )

    async def close(self) -> None:
        """Close the current WebSocket connection if open."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
