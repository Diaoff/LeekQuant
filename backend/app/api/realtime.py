from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.providers import DataProviderError
from app.db.session import get_session
from app.realtime.bus import RealtimeBus, RealtimeSubscription, RealtimeUnavailable, get_realtime_bus
from app.realtime.models import normalize_ts_code
from app.realtime.providers import EastMoneyRealtimeProvider
from app.realtime.risk_guard import get_risk_guard_status

router = APIRouter(tags=["realtime"])
logger = logging.getLogger(__name__)


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
        return {"items": [], "errors": [str(exc)]}
    except DataProviderError as exc:
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
) -> None:
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
    except (RuntimeError, WebSocketDisconnect):
        return False
    return True


async def _safe_close(websocket: WebSocket, code: int, send_lock: asyncio.Lock) -> None:
    try:
        async with send_lock:
            await websocket.close(code=code)
    except RuntimeError:
        return


async def _stop_pump(pump_task: asyncio.Task[None] | None) -> None:
    if pump_task is None or pump_task.done():
        return
    pump_task.cancel()
    try:
        await pump_task
    except (asyncio.CancelledError, RealtimeUnavailable):
        pass


@router.websocket("/ws/realtime")
async def realtime_websocket(
    websocket: WebSocket,
    bus: RealtimeBus = Depends(realtime_bus_dependency),
) -> None:
    await websocket.accept()
    subscribed: set[str] = set()
    send_lock = asyncio.Lock()
    try:
        subscription = await bus.open_subscription()
    except RealtimeUnavailable as exc:
        await _safe_send_json(websocket, {"type": "error", "detail": str(exc)}, send_lock)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    pump_task: asyncio.Task[None] | None = None
    try:
        while True:
            receive_task = asyncio.create_task(websocket.receive_json())
            wait_tasks: set[asyncio.Task[Any]] = {receive_task}
            if pump_task is not None:
                wait_tasks.add(pump_task)
            done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
            if pump_task is not None and pump_task in done:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await pump_task
                break
            try:
                message = receive_task.result()
            except WebSocketDisconnect:
                break
            if not isinstance(message, dict):
                if not await _safe_send_json(websocket, {"type": "error", "detail": "message must be a JSON object"}, send_lock):
                    break
                continue
            action = message.get("action") or message.get("type")
            try:
                codes = _parse_codes(message)
            except ValueError as exc:
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
                pump_task = asyncio.create_task(_pump_ticks(websocket, subscription, subscribed, send_lock))
            elif action == "unsubscribe":
                await _stop_pump(pump_task)
                pump_task = None
                await subscription.unsubscribe(codes & subscribed)
                subscribed.difference_update(codes)
                if not await _safe_send_json(websocket, {"type": "subscribed", "ts_codes": sorted(subscribed)}, send_lock):
                    break
                if subscribed:
                    pump_task = asyncio.create_task(_pump_ticks(websocket, subscription, subscribed, send_lock))
            else:
                if not await _safe_send_json(websocket, {"type": "error", "detail": "action must be subscribe or unsubscribe"}, send_lock):
                    break
    except RealtimeUnavailable as exc:
        await _safe_send_json(websocket, {"type": "error", "detail": str(exc)}, send_lock)
    finally:
        await _stop_pump(pump_task)
        await subscription.close()
