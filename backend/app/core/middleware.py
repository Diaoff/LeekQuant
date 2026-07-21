"""FastAPI middlewares: RequestID injection and Prometheus metrics."""
from __future__ import annotations

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from structlog.contextvars import clear_contextvars, bind_contextvars

from prometheus_client import Counter, Histogram

# Prometheus metrics — module-level singletons (registered once per process)
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=("method", "path", "status"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=("method", "path", "status"),
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject X-Request-ID into request state + structlog contextvars.

    - If client sent X-Request-ID, reuse it (truncated to 64 chars).
    - Otherwise generate a fresh UUID4 hex.
    - Echo back in response header.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request_id = request_id[:64]
        # Bind to structlog context so all log records in this request carry it
        clear_contextvars()
        bind_contextvars(request_id=request_id, method=request.method, path=request.url.path)
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count + latency per (method, path_template, status).

    Uses route path template (e.g. /api/strategies/{strategy_id}) when available
    to avoid high-cardinality label explosion from path params.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Resolve path template (e.g. /api/backtests/{id}) to avoid cardinality explosion
        route = request.scope.get("route")
        path_template = route.path if route is not None and hasattr(route, "path") else request.url.path
        # Collapse numeric/long path segments into {param} for safety
        if not path_template or "{" not in path_template:
            # Best-effort: collapse obvious numeric ids in raw path
            parts = []
            for seg in request.url.path.split("/"):
                parts.append(seg if not (seg.isdigit() or len(seg) > 32) else "{param}")
            path_template = "/".join(parts) or "/"

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration = time.perf_counter() - start
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method,
                path=path_template,
                status=str(status_code),
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=request.method,
                path=path_template,
                status=str(status_code),
            ).observe(duration)
