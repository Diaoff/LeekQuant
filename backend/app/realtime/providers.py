from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.data.providers import DataProviderError
from app.realtime.models import RealtimeTick, normalize_ts_code


class RealtimeProvider(Protocol):
    def stream(self) -> AsyncIterator[RealtimeTick]: ...


class MockRealtimeProvider:
    def __init__(self, ts_codes: list[str], interval_seconds: float = 1.0):
        self.ts_codes = [normalize_ts_code(code) for code in ts_codes]
        self.interval_seconds = interval_seconds

    async def stream(self) -> AsyncIterator[RealtimeTick]:
        step = 0
        while True:
            for index, ts_code in enumerate(self.ts_codes):
                base = Decimal("10") + Decimal(index)
                price = base + (Decimal(step % 20) * Decimal("0.01"))
                change = price - base
                yield RealtimeTick(
                    ts_code=ts_code,
                    price=price,
                    change=change,
                    change_pct=(change / base * Decimal("100")).quantize(Decimal("0.0001")),
                    volume=1000 + step,
                    amount=price * Decimal(1000 + step),
                    bid1=price - Decimal("0.01"),
                    ask1=price + Decimal("0.01"),
                    ts=datetime.now(timezone.utc),
                )
            step += 1
            await asyncio.sleep(self.interval_seconds)

    async def fetch_snapshot(self) -> list[RealtimeTick]:
        ticks = []
        async for tick in self.stream():
            ticks.append(tick)
            if len(ticks) >= len(self.ts_codes):
                return ticks
        return ticks


class EastMoneyRealtimeProvider:
    """Protocol boundary for the EastMoney feed; parsing stays outside WebSocket fanout."""

    SNAPSHOT_URLS = (
        "http://push2.eastmoney.com/api/qt/ulist.np/get",
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        "https://82.push2.eastmoney.com/api/qt/ulist.np/get",
    )

    def __init__(self, ts_codes: list[str]):
        self.ts_codes = [normalize_ts_code(code) for code in ts_codes]

    @staticmethod
    def _secid(ts_code: str) -> str:
        symbol, exchange = normalize_ts_code(ts_code).split(".", 1)
        market = "1" if exchange == "SH" else "0"
        return f"{market}.{symbol}"

    @staticmethod
    def _ts_code(row: dict[str, Any]) -> str | None:
        symbol = str(row.get("f12") or "").strip()
        market = row.get("f13")
        if not symbol:
            return None
        suffix = "SH" if int(market or 0) == 1 else "SZ"
        return f"{symbol}.{suffix}"

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        if value in (None, "-", ""):
            return None
        return Decimal(str(value))

    @classmethod
    def _tick_from_row(cls, row: dict[str, Any]) -> RealtimeTick | None:
        ts_code = cls._ts_code(row)
        price = cls._decimal(row.get("f2"))
        if ts_code is None or price is None:
            return None
        return RealtimeTick(
            ts_code=ts_code,
            price=price,
            change=cls._decimal(row.get("f4")) or Decimal("0"),
            change_pct=cls._decimal(row.get("f3")) or Decimal("0"),
            volume=int(cls._decimal(row.get("f5")) or Decimal("0")),
            amount=cls._decimal(row.get("f6")) or Decimal("0"),
            bid1=cls._decimal(row.get("f31")),
            ask1=cls._decimal(row.get("f32")),
            ts=datetime.now(timezone.utc),
        )

    async def fetch_snapshot(self) -> list[RealtimeTick]:
        if not self.ts_codes:
            return []
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f12,f13,f14,f2,f3,f4,f5,f6,f18,f31,f32",
            "secids": ",".join(self._secid(code) for code in self.ts_codes),
        }
        payload: dict[str, Any] | None = None
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            for url in self.SNAPSHOT_URLS:
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    break
                except Exception as exc:
                    last_error = exc
        if payload is None:
            raise DataProviderError(f"eastmoney realtime snapshot failed: {last_error}") from last_error
        rows = ((payload.get("data") or {}).get("diff") or []) if isinstance(payload, dict) else []
        ticks = [tick for row in rows if isinstance(row, dict) for tick in [self._tick_from_row(row)] if tick is not None]
        return [tick for tick in ticks if tick.ts_code in set(self.ts_codes)]

    async def stream(self) -> AsyncIterator[RealtimeTick]:
        from app.realtime.eastmoney_ws import EastMoneyWSClient

        client = EastMoneyWSClient(self.ts_codes)
        try:
            async for tick in client.stream():
                yield tick
        finally:
            await client.close()
