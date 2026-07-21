import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.backtests import router as backtests_router
from app.api.data import router as data_router
from app.api.factors import router as factors_router
from app.api.metrics import router as metrics_router
from app.api.preferences import router as preferences_router
from app.api.realtime import router as realtime_router
from app.api.signals import router as signals_router
from app.api.sim import router as sim_router
from app.api.sources import router as sources_router
from app.api.stocks import router as stocks_router
from app.api.strategies import router as strategies_router
from app.api.system import router as system_router
from app.api.tasks import router as tasks_router
from app.api.watchlist import router as watchlist_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.middleware import MetricsMiddleware, RequestIDMiddleware
from app.db.session import async_session_factory, engine, get_session

setup_logging()
settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from app.data.source_service import apply_config_from_db
    try:
        async with async_session_factory() as session:
            await apply_config_from_db(session)
    except Exception:
        logger.exception("Failed to apply data source config from DB on startup")
    yield
    await engine.dispose()
    # Close realtime bus Redis connection gracefully (M-12 fix)
    try:
        from app.realtime.bus import get_realtime_bus
        await get_realtime_bus().close()
    except Exception:
        logger.debug("Failed to close realtime bus on shutdown", exc_info=True)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Middlewares — order matters: outermost first.
# RequestID first so all downstream logs (including metrics) carry request_id.
app.add_middleware(RequestIDMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions — log structured + return sanitized 500.

    Prevents raw tracebacks from leaking to clients; logs the full exception
    with structlog context (request_id is already bound via middleware).
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception(
        "unhandled exception",
        error=exc.__class__.__name__,
        path=request.url.path,
        method=request.method,
        request_id=request_id,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "internal server error",
            "request_id": request_id,
        },
    )


app.include_router(data_router)
app.include_router(sources_router)
app.include_router(tasks_router)
app.include_router(stocks_router)
app.include_router(watchlist_router)
app.include_router(strategies_router)
app.include_router(backtests_router)
app.include_router(signals_router)
app.include_router(sim_router)
app.include_router(factors_router)
app.include_router(preferences_router)
app.include_router(realtime_router)
app.include_router(system_router)
app.include_router(metrics_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "leek-quant-backend",
        "environment": settings.environment,
    }


async def _ping_db(session: AsyncSession) -> str:
    await session.execute(text("SELECT 1"))
    return "ok"


async def _ping_redis() -> str:
    """Ping Redis with a short connect/timeout so health endpoint fails fast.

    Opens a fresh, short-lived client so it works even when the realtime bus
    client is shared / not yet initialised.
    """
    import redis.asyncio as redis_async

    client = redis_async.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()
    return "ok"


def _ok_str(result: Any) -> str:
    """Reduce an asyncio.gather(return_exceptions=True) result to ok/fail."""
    return "fail" if isinstance(result, Exception) else "ok"


@app.get("/api/health/db", tags=["health"])
async def database_health(
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | int]:
    try:
        result = await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unavailable: {exc.__class__.__name__}",
        ) from exc

    return {
        "status": "ok",
        "database": "postgresql",
        "result": int(result.scalar_one()),
    }


@app.get("/api/health/redis", tags=["health"])
async def redis_health() -> dict[str, str]:
    """Standalone Redis health check — used by Docker healthchecks for services
    that only depend on Redis (no DB connectivity required)."""
    try:
        await _ping_redis()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"redis unavailable: {exc.__class__.__name__}",
        ) from exc

    return {"status": "ok", "redis": "reachable"}


@app.get("/api/health", tags=["health"])
async def aggregate_health(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Aggregate health check — parallel-ping DB + Redis.

    Returns ``status: ok`` only when both checks succeed; ``degraded``
    otherwise so Docker healthcheck can fail when Redis (used by WS /
    BeatLock / event push) is down even if DB is up.
    """
    db_result, redis_result = await asyncio.gather(
        _ping_db(session),
        _ping_redis(),
        return_exceptions=True,
    )
    overall = (
        "ok"
        if not isinstance(db_result, Exception) and not isinstance(redis_result, Exception)
        else "degraded"
    )
    return {
        "status": overall,
        "checks": {"db": _ok_str(db_result), "redis": _ok_str(redis_result)},
    }
