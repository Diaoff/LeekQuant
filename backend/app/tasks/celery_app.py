from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "leek_quant",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.data_tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    beat_schedule={
        "daily-incremental-kline": {
            "task": "app.tasks.data_tasks.incremental_kline_update",
            "schedule": 60 * 60 * 24,
        },
        "daily-fundamentals": {
            "task": "app.tasks.data_tasks.sync_fundamentals",
            "schedule": 60 * 60 * 24,
        }
    },
)
