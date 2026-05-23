from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

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
    timezone="Asia/Shanghai",
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
            "schedule": crontab(hour=18, minute=0),
        },
        "generate-signals-daily": {
            "task": "app.tasks.signal_tasks.generate_all_signals",
            "schedule": crontab(hour=17, minute=0),
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
            "schedule": crontab(hour=15, minute=5),
        },
        "snapshot-sim-nav-daily": {
            "task": "app.tasks.trading_tasks.snapshot_nav_daily",
            "schedule": crontab(hour=15, minute=20),
        },
    },
)
