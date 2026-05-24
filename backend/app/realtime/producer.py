from __future__ import annotations

import argparse
import asyncio

from app.realtime.bus import RealtimeBus, get_realtime_bus
from app.realtime.providers import MockRealtimeProvider


async def run_mock_producer(ts_codes: list[str], interval_seconds: float, bus: RealtimeBus | None = None) -> None:
    provider = MockRealtimeProvider(ts_codes=ts_codes, interval_seconds=interval_seconds)
    realtime_bus = bus or get_realtime_bus()
    async for tick in provider.stream():
        await realtime_bus.publish(tick)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish mock realtime ticks to Redis Pub/Sub.")
    parser.add_argument("ts_codes", nargs="+", help="A-share ts_code values, e.g. 000001.SZ 600000.SH")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between mock batches")
    args = parser.parse_args()
    asyncio.run(run_mock_producer(args.ts_codes, args.interval))


if __name__ == "__main__":
    main()

