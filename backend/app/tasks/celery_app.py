import asyncio
import logging
from datetime import timedelta

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready
from kombu import Queue

from app.core.config import get_settings
from app.data.repository import mark_stale_running_task_runs
from app.db.session import async_session_factory

settings = get_settings()
logger = logging.getLogger(__name__)

celery_app = Celery(
    "leek_quant",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.data_tasks",
        "app.backtest.tasks",
        "app.tasks.trading_tasks",
        "app.tasks.signal_tasks",
        "app.tasks.factor_tasks",
    ],
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    timezone="Asia/Shanghai",
    task_default_queue="default",
    task_queues=(
        Queue("default"),
        Queue("data"),
        Queue("backtest"),
        Queue("factor"),
        Queue("trading"),
    ),
    task_routes={
        "app.tasks.data_tasks.*": {"queue": "data"},
        "app.tasks.run_backtest": {"queue": "backtest"},
        "app.tasks.factor_tasks.*": {"queue": "factor"},
        "app.tasks.trading_tasks.*": {"queue": "trading"},
        "app.tasks.signal_tasks.*": {"queue": "trading"},
    },
    beat_schedule={
        "update-stock-basic-weekly": {
            "task": "app.tasks.data_tasks.update_stock_basic",
            "schedule": crontab(day_of_week="saturday", hour=3, minute=0),
        },
        "update-trade-calendar-weekly": {
            "task": "app.tasks.data_tasks.update_trade_calendar",
            "schedule": crontab(day_of_week="sunday", hour=2, minute=0),
        },
        "incremental-kline-daily": {
            "task": "app.tasks.data_tasks.incremental_kline_update",
            "schedule": crontab(hour=17, minute=0),
        },
        "generate-signals-daily": {
            "task": "app.tasks.signal_tasks.generate_all_signals",
            "schedule": crontab(hour=12, minute=0),
        },
        "compute-factors-daily": {
            "task": "app.tasks.factor_tasks.compute_daily_factors",
            "schedule": crontab(hour=17, minute=30),
        },
        "update-fundamentals-daily": {
            "task": "app.tasks.data_tasks.sync_fundamentals",
            "schedule": crontab(hour=19, minute=30),
        },
        "unlock-t1-positions-daily": {
            "task": "app.tasks.trading_tasks.unlock_t1_daily",
            "schedule": crontab(hour=9, minute=25),
        },
        "match-pending-orders-daily": {
            "task": "app.tasks.trading_tasks.match_pending_orders",
            "schedule": crontab(hour=17, minute=5),
        },
        "snapshot-sim-nav-daily": {
            "task": "app.tasks.trading_tasks.snapshot_nav_daily",
            "schedule": crontab(hour=15, minute=20),
        },
    },
)


@worker_ready.connect
def cleanup_stale_running_tasks_on_worker_ready(**_kwargs) -> None:
    async def cleanup() -> int:
        async with async_session_factory() as session:
            return await mark_stale_running_task_runs(
                session,
                older_than=timedelta(hours=24),
                error_message="stale running task after celery worker startup",
            )

    try:
        cleaned = asyncio.run(cleanup())
    except Exception:
        logger.exception("Failed to clean stale running task records on worker startup")
        return
    if cleaned:
        logger.warning("Marked %s stale running task record(s) as failed", cleaned)
