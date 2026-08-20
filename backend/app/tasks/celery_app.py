import json
import logging
from datetime import timedelta

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure, task_prerun, task_revoked, task_success, worker_process_init, worker_ready
from kombu import Queue

from app.core.config import get_settings
from app.data.repository import mark_stale_running_task_runs
from app.db.session import async_session_factory

settings = get_settings()
logger = logging.getLogger(__name__)

TASK_EVENTS_CHANNEL = "celery:task_events"

celery_app = Celery(
    "leek_quant",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.data_tasks",
        "app.backtest.tasks",
        "app.tasks.trading_tasks",
        "app.tasks.signal_tasks",
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
        Queue("trading"),
    ),
    task_routes={
        "app.tasks.data_tasks.*": {"queue": "data"},
        "app.tasks.run_backtest": {"queue": "backtest"},
        "app.tasks.trading_tasks.*": {"queue": "trading"},
        "app.tasks.signal_tasks.*": {"queue": "trading"},
    },
    task_time_limit=settings.celery_task_time_limit,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=60,
    worker_max_tasks_per_child=50,
    result_expires=3600,
    result_persistent=False,
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
            "task": "app.tasks.data_tasks.kline_sync_dispatch",
            "schedule": crontab(hour=16, minute=0),
            "kwargs": {"job_type": "incremental"},
        },
        "incremental-kline-evening": {
            "task": "app.tasks.data_tasks.kline_sync_dispatch",
            "schedule": crontab(hour=21, minute=0),
            "kwargs": {"job_type": "incremental"},
        },
        "full-kline-weekly": {
            "task": "app.tasks.data_tasks.kline_sync_dispatch",
            "schedule": crontab(day_of_week="sunday", hour=4, minute=0),
            "kwargs": {"job_type": "full"},
        },
        "generate-signals-daily": {
            "task": "app.tasks.signal_tasks.generate_all_signals",
            "schedule": crontab(hour=12, minute=0),
        },
        "update-fundamentals-daily": {
            "task": "app.tasks.data_tasks.sync_fundamentals",
            "schedule": crontab(hour=19, minute=30),
        },
        "sync-fund-flow-daily": {
            "task": "app.tasks.data_tasks.sync_fund_flow_task",
            "schedule": crontab(hour=20, minute=0),
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
        "cleanup-stale-task-runs-hourly": {
            "task": "app.tasks.data_tasks.cleanup_stale_task_runs",
            "schedule": crontab(minute=15),  # hourly at :15
        },
        "kline-sync-recover-stuck": {
            "task": "app.tasks.data_tasks.kline_sync_recover_stuck",
            "schedule": timedelta(seconds=settings.kline_sync_recover_interval_seconds),
        },
    },
)


@worker_process_init.connect
def init_async_loop(**_kwargs) -> None:
    """Create one event loop per worker process and keep it alive.

    Every Celery task body drives the async SQLAlchemy engine through this
    loop (via ``app.core.asyncio_runtime.run_async``). Reusing a single loop
    for the process lifetime prevents "Task ... attached to a different loop"
    errors that ``asyncio.run`` (which closes the loop each call) would cause
    on a module-level async engine.
    """
    from app.core.asyncio_runtime import get_loop

    get_loop()


@worker_ready.connect
def cleanup_stale_running_tasks_on_worker_ready(**_kwargs) -> None:
    async def cleanup() -> int:
        async with async_session_factory() as session:
            return await mark_stale_running_task_runs(
                session,
                older_than=timedelta(hours=24),
                error_message="stale running task after celery worker startup",
            )

    from app.core.asyncio_runtime import run_async

    try:
        cleaned = run_async(cleanup())
    except Exception:
        logger.exception("Failed to clean stale running task records on worker startup")
        return
    if cleaned:
        logger.warning("Marked %s stale running task record(s) as failed", cleaned)

    # Kick off one stuck-item recovery pass immediately so items left running
    # by a crashed worker are reset to pending without waiting a full interval.
    _trigger_recover_on_startup()


def _trigger_recover_on_startup() -> None:
    try:
        celery_app.send_task("app.tasks.data_tasks.kline_sync_recover_stuck")
        logger.info("Enqueued initial kline_sync_recover_stuck on worker startup")
    except Exception:
        logger.debug("Could not enqueue initial kline_sync_recover_stuck", exc_info=True)


def _publish_task_event(status: str, task_id: str | None, task_name: str, **extra) -> None:
    """Publish a task lifecycle event to Redis for WebSocket fanout."""
    import redis as redis_sync

    try:
        client = redis_sync.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        payload = {
            "type": "task_status",
            "task_name": str(task_name) if task_name else "",
            "task_id": str(task_id) if task_id else "",
            "status": status,
            **extra,
        }
        client.publish(TASK_EVENTS_CHANNEL, json.dumps(payload, ensure_ascii=False, default=str))
        client.close()
    except Exception:
        logger.debug("Failed to publish task event to Redis", exc_info=True)


@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, **kwargs) -> None:
    _publish_task_event("started", task_id, sender)


@task_success.connect
def on_task_success(sender=None, result=None, **kwargs) -> None:
    task_id = kwargs.get("task_id")
    _publish_task_event("success", task_id, sender, result=str(result) if result is not None else None)
    _reconcile_task_run(
        task_id,
        "success",
        result=result if isinstance(result, dict) else None,
    )


@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, **kwargs) -> None:
    _publish_task_event("failed", task_id, sender, error=str(exception) if exception else None)
    _reconcile_task_run(
        task_id,
        "failed",
        error_message=str(exception) if exception else "task failed",
    )


@task_revoked.connect
def on_task_revoked(sender=None, request=None, **kwargs) -> None:
    task_id = getattr(request, "id", None) or kwargs.get("task_id")
    if task_id:
        _reconcile_task_run(task_id, "failed", error_message="task revoked")


def _reconcile_task_run(
    task_id: str | None,
    status: str,
    *,
    error_message: str | None = None,
    result: dict | None = None,
) -> None:
    """Backstop for task_runs status drift.

    The task body's own ``_run_tracked`` bookkeeping is the primary writer, but
    it runs *inside* the (time-limited) task body and can be skipped when the
    body is killed (e.g. SoftTimeLimitExceeded) — leaving the row stuck at
    'running' while Celery reports the real terminal state. This runs from the
    Celery signal (after the body has exited) with a fresh DB session, so it is
    not affected by the kill. It is idempotent: it only touches rows that are
    still non-terminal, so it never overwrites a status the body already wrote.
    """
    if not task_id:
        return
    try:
        from app.core.asyncio_runtime import run_async
        from app.data.repository import reconcile_task_run_status

        async def _run() -> None:
            async with async_session_factory() as session:
                await reconcile_task_run_status(
                    session,
                    task_id=task_id,
                    status=status,
                    error_message=error_message,
                    result=result,
                )

        run_async(_run())
    except Exception:
        logger.debug("Failed to reconcile task_runs for %s", task_id, exc_info=True)
