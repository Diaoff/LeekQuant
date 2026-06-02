from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.backtests import router as backtests_router
from app.api.data import router as data_router
from app.api.factors import router as factors_router
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
from app.db.session import async_session_factory, get_session

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from app.data.source_service import apply_config_from_db
    try:
        async with async_session_factory() as session:
            await apply_config_from_db(session)
    except Exception:
        pass
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "leek-quant-backend",
        "environment": settings.environment,
    }


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
