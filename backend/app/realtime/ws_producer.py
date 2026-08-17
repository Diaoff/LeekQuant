"""Standalone asyncio service: EastMoney WebSocket → Redis Pub/Sub.

Runs as a Docker container or standalone process. Connects to the EastMoney
WebSocket push feed, parses ticks, and publishes them to the Redis Pub/Sub
bus for consumption by FastAPI WebSocket endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from typing import TYPE_CHECKING

from app.realtime.bus import RealtimeBus, get_realtime_bus
from app.realtime.eastmoney_ws import EastMoneyWSClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def run_ws_producer(
    ts_codes: list[str],
    bus: RealtimeBus | None = None,
) -> None:
    """Publish EastMoney WS ticks to Redis Pub/Sub for a fixed set of codes."""
    realtime_bus = bus or get_realtime_bus()
    client = EastMoneyWSClient(ts_codes)
    logger.info("Starting EastMoney WS producer for %d codes", len(ts_codes))

    try:
        async for tick in client.stream():
            try:
                await realtime_bus.publish(tick)
            except Exception:
                logger.exception("Failed to publish tick %s", tick.ts_code)
    finally:
        await client.close()


async def _load_dynamic_codes(session_factory) -> list[str]:
    """Query DB for active watchlist + sim_positions codes, deduplicated."""
    from sqlalchemy import text

    codes: set[str] = set()
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT DISTINCT ts_code FROM watchlist_items WHERE is_active = true")
        )
        for row in result:
            ts_code = row[0]
            if ts_code:
                codes.add(ts_code)

        result = await session.execute(
            text(
                "SELECT DISTINCT p.ts_code FROM sim_positions p "
                "JOIN sim_accounts a ON a.id = p.account_id "
                "WHERE a.is_active = true AND p.shares > 0"
            )
        )
        for row in result:
            ts_code = row[0]
            if ts_code:
                codes.add(ts_code)

    return sorted(codes)


async def run_dynamic_ws_producer(
    reload_interval: float = 300.0,
    bus: RealtimeBus | None = None,
    session_factory=None,
) -> None:
    """Continuously reload stock codes from DB and stream via EastMoney WS.

    Args:
        reload_interval: Seconds between dynamic code reloads.
        bus: Optional RealtimeBus for publishing ticks. Defaults to global bus.
        session_factory: Optional async_sessionmaker. If None, lazily imports
            ``app.db.session.async_session_factory`` on first use. Lazy import
            is avoided during normal entry because importing ``app.db.session``
            triggers SQLAlchemy ``create_async_engine`` which initialises
            asyncio primitives that must be created inside the running loop.
            Callers in tests should pass a fake factory to avoid DB coupling;
            callers in production should pass the real factory explicitly.
    """
    realtime_bus = bus or get_realtime_bus()

    if session_factory is None:
        from app.db.session import async_session_factory as session_factory

    codes = await _load_dynamic_codes(session_factory)
    if not codes:
        logger.warning("No dynamic codes found, waiting for reload...")

    stop = asyncio.Event()

    def _handle_signal() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except (NotImplementedError, RuntimeError):
            # Signal handling is best-effort; not supported on all platforms
            # (e.g. Windows, some test environments).
            logger.debug("silent except in run_dynamic_ws_producer")
            pass

    logger.info("Dynamic WS producer started, initial codes: %d", len(codes))

    retry_count = 0
    while not stop.is_set():
        if codes:
            client = EastMoneyWSClient(codes)

            async def _stream_and_publish() -> None:
                async for tick in client.stream():
                    if stop.is_set():
                        await client.close()
                        return
                    try:
                        await realtime_bus.publish(tick)
                    except Exception:
                        logger.exception("Failed to publish tick %s", tick.ts_code)

            stream_task = asyncio.create_task(_stream_and_publish())
            reload_task = asyncio.create_task(asyncio.sleep(reload_interval))

            done, pending = await asyncio.wait(
                {stream_task, reload_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel pending sibling and await its cleanup so client.close() runs
            for task in pending:
                task.cancel()
                try:
                    await asyncio.gather(task, return_exceptions=True)
                except Exception:
                    logger.debug("silent except in run_dynamic_ws_producer")
                    pass
            # Explicit close in case the inner coroutine didn't get to it
            try:
                await client.close()
            except Exception:
                logger.exception("client.close() failed during cleanup")

            # If stream_task failed (not cancelled), reconnect with exponential backoff
            # instead of waiting full reload_interval.
            if stream_task in done and not stream_task.cancelled():
                exc = stream_task.exception()
                if exc is not None:
                    backoff = min(2 ** retry_count, 30)
                    logger.exception(
                        "Stream task failed, reconnecting in %.1fs (retry #%d)",
                        backoff,
                        retry_count,
                    )
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=backoff)
                        break  # stop signaled during backoff
                    except asyncio.TimeoutError:
                        logger.debug("silent except in run_dynamic_ws_producer")
                        pass
                    retry_count += 1
                    continue
            retry_count = 0  # success or clean reload → reset backoff
        else:
            await asyncio.sleep(reload_interval)

        if stop.is_set():
            break

        new_codes = await _load_dynamic_codes(session_factory)
        if new_codes != codes:
            logger.info("Dynamic codes updated: %d → %d", len(codes), len(new_codes))
            codes = new_codes


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="EastMoney WS → Redis realtime producer.")
    parser.add_argument("ts_codes", nargs="*", help="Stock codes. Empty with --dynamic to auto-load from DB.")
    parser.add_argument("--dynamic", action="store_true", help="Continuously reload codes from DB.")
    parser.add_argument(
        "--reload-interval",
        type=float,
        default=300.0,
        help="Seconds between dynamic code reloads (default: 300).",
    )
    args = parser.parse_args()

    if args.dynamic:
        asyncio.run(run_dynamic_ws_producer(reload_interval=args.reload_interval))
    elif args.ts_codes:
        asyncio.run(run_ws_producer(args.ts_codes))
    else:
        parser.error("Provide ts_codes or use --dynamic")


if __name__ == "__main__":
    main()
