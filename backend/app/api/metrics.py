"""Prometheus /metrics endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import get_settings

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    """Expose Prometheus metrics.

    Disabled at runtime via METRICS_ENABLED=false to short-circuit collection
    in dev environments where the route is not wired.
    """
    if not get_settings().metrics_enabled:
        return Response(content="", media_type=CONTENT_TYPE_LATEST)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
