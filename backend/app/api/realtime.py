from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.data.providers import DataProviderError
from app.db.session import get_session
from app.realtime.bus import RealtimeBus, RealtimeSubscription, RealtimeUnavailable, get_realtime_bus
from app.realtime.models import normalize_ts_code
from app.realtime.providers import EastMoneyRealtimeProvider
from app.realtime.risk_guard import get_risk_guard_status

router = APIRouter(tags=["realtime"])
logger = logging.getLogger(__name__)

TASK_EVENTS_CHANNEL = "celery:task_events"
SIGNAL_EVENTS_CHANNEL = "signal:new"


async def realtime_bus_dependency() -> RealtimeBus:
    return get_realtime_bus()


def realtime_provider_factory(ts_codes: list[str]) -> EastMoneyRealtimeProvider:
    return EastMoneyRealtimeProvider(ts_codes)


def _parse_codes(message: dict[str, Any]) -> set[str]:
    raw_codes = message.get("ts_codes")
    if isinstance(raw_codes, str):
        raw_codes = [raw_codes]
    if not isinstance(raw_codes, list):
        raise ValueError("ts_codes must be a non-empty string or list")
    codes = {normalize_ts_code(str(code)) for code in raw_codes if str(code).strip()}
    if not codes:
        raise ValueError("ts_codes must not be empty")
    return codes


def _parse_query_codes(ts_codes: str) -> list[str]:
    codes = [normalize_ts_code(code) for code in ts_codes.split(",") if code.strip()]
    if not codes:
        raise ValueError("ts_codes must not be empty")
    return sorted(set(codes))


@router.get("/api/realtime/snapshot")
async def realtime_snapshot(ts_codes: str) -> dict[str, Any]:
    try:
        codes = _parse_query_codes(ts_codes)
        provider = realtime_provider_factory(codes)
        ticks = await provider.fetch_snapshot()
    except ValueError as exc:
        logger.debug("silent except in realtime_snapshot (exc): %s", exc)
        return {"items": [], "errors": [str(exc)]}
    except DataProviderError as exc:
        logger.debug("silent except in realtime_snapshot (exc): %s", exc)
        return {"items": [], "errors": [str(exc)]}
    return {"items": [tick.to_payload() for tick in ticks], "errors": []}


@router.get("/api/realtime/risk-guard/status")
async def realtime_risk_guard_status(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await get_risk_guard_status(session)


async def _pump_ticks(
    websocket: WebSocket,
    subscription: RealtimeSubscription,
    subscribed: set[str],
    send_lock: asyncio.Lock,
    queue: asyncio.Queue | None = None,
    dropped_counter: dict[str, int] | None = None,
) -> None:
    """Pump ticks from subscription to websocket with backpressure.

    When `queue` is provided, runs as producer+consumer:
    - producer: pulls ticks from subscription.listen() and puts to queue
      (drops + increments counter when queue is full)
    - consumer: gets ticks from queue and sends via _safe_send_json
      (closes connection on send timeout)

    When `queue` is None (backpressure disabled), falls back to direct send.
    """
    if queue is None:
        try:
            async for tick in subscription.listen():
                if tick.ts_code in subscribed:
                    if not await _safe_send_json(websocket, tick.to_payload(), send_lock):
                        break
        except asyncio.CancelledError:
            raise
        except RealtimeUnavailable as exc:
            logger.exception("Realtime subscription failed")
            await _safe_send_json(websocket, {"type": "error", "detail": str(exc)}, send_lock)
            await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR, send_lock)
        except Exception:
            logger.exception("Unexpected realtime subscription failure")
            await _safe_send_json(websocket, {"type": "error", "detail": "realtime stream failed"}, send_lock)
            await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR, send_lock)
        return

    if dropped_counter is None:
        dropped_counter = {"count": 0}
    settings = get_settings()
    send_timeout = settings.ws_send_timeout_seconds

    async def producer() -> None:
        try:
            async for tick in subscription.listen():
                if tick.ts_code not in subscribed:
                    continue
                try:
                    queue.put_nowait(tick)
                except asyncio.QueueFull:
                    dropped_counter["count"] += 1
                    logger.warning(
                        "ws queue full, dropped tick %s (total dropped: %d)",
                        tick.ts_code,
                        dropped_counter["count"],
                    )
                    if dropped_counter["count"] % 10 == 0:
                        await _safe_send_json(
                            websocket,
                            {"type": "dropped", "count": dropped_counter["count"]},
                            send_lock,
                        )
        except asyncio.CancelledError:
            raise
        except RealtimeUnavailable as exc:
            logger.exception("Realtime subscription failed")
            await _safe_send_json(websocket, {"type": "error", "detail": str(exc)}, send_lock)
            await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR, send_lock)
        except Exception:
            logger.exception("Unexpected realtime subscription failure")
            await _safe_send_json(websocket, {"type": "error", "detail": "realtime stream failed"}, send_lock)
            await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR, send_lock)
        finally:
            # Sentinel: tell consumer to stop so it doesn't block forever on queue.get().
            # put_nowait is safe here — if the queue is full we still want to try once;
            # on failure consumer will eventually be cancelled by the outer task.
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                logger.debug("silent except in producer")
                pass

    async def consumer() -> None:
        while True:
            tick = await queue.get()
            if tick is None:  # sentinel from producer: stop cleanly
                return
            try:
                ok = await asyncio.wait_for(
                    _safe_send_json(websocket, tick.to_payload(), send_lock),
                    timeout=send_timeout,
                )
                if not ok:
                    return
            except asyncio.TimeoutError:
                logger.warning(
                    "ws send timeout after %ss, closing connection",
                    send_timeout,
                )
                await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR, send_lock)
                return
            except asyncio.CancelledError:
                raise

    await asyncio.gather(producer(), consumer())


async def _safe_send_json(
    websocket: WebSocket,
    payload: dict[str, Any],
    send_lock: asyncio.Lock | None = None,
) -> bool:
    try:
        if send_lock is None:
            await websocket.send_json(payload)
        else:
            async with send_lock:
                await websocket.send_json(payload)
    except asyncio.CancelledError:
        # Propagate cancellation; do not swallow the signal.
        raise
    except (RuntimeError, WebSocketDisconnect, ConnectionResetError, BrokenPipeError) as exc:
        logger.debug("websocket send failed: %s", exc)
        return False
    except Exception:
        # Catch-all: covers anyio.BrokenResourceError, AttributeError, etc.
        # that would otherwise terminate the pump task.
        logger.exception("unexpected websocket send failure")
        return False
    return True


async def _safe_close(websocket: WebSocket, code: int, send_lock: asyncio.Lock) -> None:
    try:
        async with send_lock:
            await websocket.close(code=code)
    except RuntimeError:
        logger.debug("silent except in _safe_close")
        return


async def _stop_pump(pump_task: asyncio.Task[None] | None) -> None:
    if pump_task is None or pump_task.done():
        return
    pump_task.cancel()
    try:
        await pump_task
    except (asyncio.CancelledError, RealtimeUnavailable):
        logger.debug("silent except in _stop_pump")
        pass


async def _heartbeat_loop(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    stop_event: asyncio.Event,
) -> None:
    """Periodically send ping frames to detect half-open connections.

    When ping send times out or fails, closes the websocket and signals stop.
    Disabled when ws_ping_interval_seconds <= 0.
    """
    settings = get_settings()
    if settings.ws_ping_interval_seconds <= 0:
        return
    ping_timeout = settings.ws_ping_timeout_seconds
    while not stop_event.is_set():
        try:
            await asyncio.sleep(settings.ws_ping_interval_seconds)
        except asyncio.CancelledError:
            raise
        if stop_event.is_set():
            return
        try:
            ok = await asyncio.wait_for(
                _safe_send_json(websocket, {"type": "ping", "ts": int(time.time())}, send_lock),
                timeout=ping_timeout,
            )
            if not ok:
                logger.warning("ws heartbeat send failed, closing connection")
                await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR, send_lock)
                stop_event.set()
                return
        except asyncio.TimeoutError:
            logger.warning(
                "ws heartbeat timeout after %ss, closing connection",
                ping_timeout,
            )
            await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR, send_lock)
            stop_event.set()
            return
        except asyncio.CancelledError:
            raise


@router.websocket("/ws/realtime")
async def realtime_websocket(
    websocket: WebSocket,
    replay_from: str | None = Query(default=None, alias="replay_from"),
    bus: RealtimeBus = Depends(realtime_bus_dependency),
) -> None:
    await websocket.accept()
    subscribed: set[str] = set()
    send_lock = asyncio.Lock()
    settings = get_settings()
    try:
        subscription = await bus.open_subscription(replay_from=replay_from)
    except RealtimeUnavailable as exc:
        logger.warning("silent except in realtime_websocket (exc)", exc_info=True)
        await _safe_send_json(websocket, {"type": "error", "detail": str(exc)}, send_lock)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    pump_task: asyncio.Task[None] | None = None
    stop_event = asyncio.Event()
    heartbeat_task: asyncio.Task[None] | None = None
    if settings.ws_ping_interval_seconds > 0:
        heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, send_lock, stop_event))
    # Backpressure state: one queue per WebSocket (re-created on each subscribe).
    # Kept at endpoint scope so the queue is shared across re-subscribes within
    # the same connection.
    queue: asyncio.Queue = asyncio.Queue(maxsize=settings.ws_queue_maxsize)
    dropped_counter: dict[str, int] = {"count": 0}
    try:
        while True:
            receive_task = asyncio.create_task(websocket.receive_json())
            wait_tasks: set[asyncio.Task[Any]] = {receive_task}
            if pump_task is not None:
                wait_tasks.add(pump_task)
            if heartbeat_task is not None:
                wait_tasks.add(heartbeat_task)
            done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
            # If heartbeat or pump exits, tear down the whole connection.
            if (pump_task is not None and pump_task in done) or (
                heartbeat_task is not None and heartbeat_task in done
            ):
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if pump_task is not None and pump_task in done:
                    await pump_task
                break
            try:
                message = receive_task.result()
            except WebSocketDisconnect:
                logger.debug("silent except in realtime_websocket")
                break
            if not isinstance(message, dict):
                if not await _safe_send_json(websocket, {"type": "error", "detail": "message must be a JSON object"}, send_lock):
                    break
                continue
            action = message.get("action") or message.get("type")
            try:
                codes = _parse_codes(message)
            except ValueError as exc:
                logger.warning("silent except in realtime_websocket (exc)", exc_info=True)
                if not await _safe_send_json(websocket, {"type": "error", "detail": str(exc)}, send_lock):
                    break
                continue
            if action == "subscribe":
                await _stop_pump(pump_task)
                pump_task = None
                await subscription.subscribe(codes - subscribed)
                subscribed.update(codes)
                if not await _safe_send_json(websocket, {"type": "subscribed", "ts_codes": sorted(subscribed)}, send_lock):
                    break
                # Fresh queue for the new subscription window.
                queue = asyncio.Queue(maxsize=settings.ws_queue_maxsize)
                dropped_counter = {"count": 0}
                pump_task = asyncio.create_task(
                    _pump_ticks(websocket, subscription, subscribed, send_lock, queue, dropped_counter)
                )
            elif action == "unsubscribe":
                await _stop_pump(pump_task)
                pump_task = None
                await subscription.unsubscribe(codes & subscribed)
                subscribed.difference_update(codes)
                if not await _safe_send_json(websocket, {"type": "subscribed", "ts_codes": sorted(subscribed)}, send_lock):
                    break
                if subscribed:
                    queue = asyncio.Queue(maxsize=settings.ws_queue_maxsize)
                    dropped_counter = {"count": 0}
                    pump_task = asyncio.create_task(
                        _pump_ticks(websocket, subscription, subscribed, send_lock, queue, dropped_counter)
                    )
            else:
                if not await _safe_send_json(websocket, {"type": "error", "detail": "action must be subscribe or unsubscribe"}, send_lock):
                    break
    except RealtimeUnavailable as exc:
        logger.warning("silent except in realtime_websocket (exc)", exc_info=True)
        await _safe_send_json(websocket, {"type": "error", "detail": str(exc)}, send_lock)
    finally:
        stop_event.set()
        await _stop_pump(pump_task)
        if heartbeat_task is not None and not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                logger.debug("silent except in realtime_websocket")
                pass
        await subscription.close()


async def _redis_channel_pump(
    websocket: WebSocket,
    channel: str,
    send_lock: asyncio.Lock,
) -> None:
    """Subscribe to a Redis Pub/Sub channel and forward messages to the WebSocket."""
    import redis.asyncio as redis_async

    from app.core.config import get_settings

    client = redis_async.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=5,
    )
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            if not await _safe_send_json(websocket, {"type": "raw", "channel": channel, "data": data}, send_lock):
                break
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Redis channel pump failed for %s", channel)
        await _safe_send_json(websocket, {"type": "error", "detail": f"channel {channel} stream failed"}, send_lock)
        await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR, send_lock)
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            logger.debug("silent except in _redis_channel_pump")
            pass
        await pubsub.close()
        await client.close()


def _create_websocket_handler(channel: str):
    async def handler(websocket: WebSocket) -> None:
        await websocket.accept()
        send_lock = asyncio.Lock()
        settings = get_settings()
        pump_task: asyncio.Task[None] | None = None
        stop_event = asyncio.Event()
        heartbeat_task: asyncio.Task[None] | None = None
        if settings.ws_ping_interval_seconds > 0:
            heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, send_lock, stop_event))
        try:
            while True:
                receive_task = asyncio.create_task(websocket.receive_json())
                wait_tasks: set[asyncio.Task[Any]] = {receive_task}
                if pump_task is not None:
                    wait_tasks.add(pump_task)
                if heartbeat_task is not None:
                    wait_tasks.add(heartbeat_task)
                done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
                # If heartbeat or pump exits, tear down the whole connection.
                if (pump_task is not None and pump_task in done) or (
                    heartbeat_task is not None and heartbeat_task in done
                ):
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    break
                try:
                    message = receive_task.result()
                except WebSocketDisconnect:
                    logger.debug("silent except in handler")
                    break
                if not isinstance(message, dict):
                    if not await _safe_send_json(websocket, {"type": "error", "detail": "message must be a JSON object"}, send_lock):
                        break
                    continue
                action = message.get("action") or message.get("type")
                if action == "subscribe":
                    if pump_task is not None and not pump_task.done():
                        pump_task.cancel()
                        try:
                            await pump_task
                        except asyncio.CancelledError:
                            logger.debug("silent except in handler")
                            pass
                    if not await _safe_send_json(websocket, {"type": "subscribed", "channel": channel}, send_lock):
                        break
                    pump_task = asyncio.create_task(_redis_channel_pump(websocket, channel, send_lock))
                elif action == "unsubscribe":
                    if pump_task is not None and not pump_task.done():
                        pump_task.cancel()
                        try:
                            await pump_task
                        except asyncio.CancelledError:
                            logger.debug("silent except in handler")
                            pass
                    pump_task = None
                    if not await _safe_send_json(websocket, {"type": "unsubscribed", "channel": channel}, send_lock):
                        break
                else:
                    if not await _safe_send_json(websocket, {"type": "error", "detail": "action must be subscribe or unsubscribe"}, send_lock):
                        break
        finally:
            stop_event.set()
            if pump_task is not None and not pump_task.done():
                pump_task.cancel()
                try:
                    await pump_task
                except asyncio.CancelledError:
                    logger.debug("silent except in handler")
                    pass
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    logger.debug("silent except in handler")
                    pass
    return handler


router.add_websocket_route("/ws/tasks", _create_websocket_handler(TASK_EVENTS_CHANNEL))
router.add_websocket_route("/ws/signals", _create_websocket_handler(SIGNAL_EVENTS_CHANNEL))
