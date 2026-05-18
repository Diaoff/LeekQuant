from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.backtests import router as backtests_router
from app.api.data import router as data_router
from app.api.pools import router as pools_router
from app.api.stocks import router as stocks_router
from app.api.strategies import router as strategies_router
from app.api.tasks import router as tasks_router
from app.api.watchlist import router as watchlist_router
from app.core.config import get_settings
from app.db.session import get_session

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data_router)
app.include_router(tasks_router)
app.include_router(stocks_router)
app.include_router(watchlist_router)
app.include_router(pools_router)
app.include_router(strategies_router)
app.include_router(backtests_router)


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
