"""Data-access repository package.

Historically a single 1000+ line ``repository.py`` (one raw-SQL function per
table/entity). Split into per-entity submodules for maintainability while
keeping backward-compatible re-exports so every ``from app.data.repository
import <fn>`` / ``from app.data import repository`` call site keeps working.

Grouping:
- ``stock``           -> stock_basic upserts / cleanup
- ``calendar``        -> trade_calendar upserts
- ``kline``           -> daily_kline upserts + sync progress
- ``fundamentals``    -> stock_fundamentals upserts
- ``alerts``          -> alert_events CRUD
- ``task_runs``       -> task_runs lifecycle
- ``kline_sync``      -> kline_sync_jobs / kline_sync_items queue
"""
from app.data.repository.alerts import (
    create_alert,
    list_alerts,
    resolve_alert,
)
from app.data.repository.calendar import (
    upsert_trade_calendar,
)
from app.data.repository.fundamentals import (
    upsert_stock_fundamentals,
)
from app.data.repository.fund_flow import (
    get_recent_fund_flow,
    upsert_fund_flow,
)
from app.data.repository.kline import (
    get_active_stock_codes,
    get_sync_progress,
    upsert_daily_kline,
)
from app.data.repository.kline_sync import (
    claim_kline_sync_items,
    complete_job_if_done,
    create_kline_sync_job,
    get_job_progress,
    insert_kline_sync_items,
    list_job_items,
    list_recent_jobs,
    mark_item_done,
    mark_item_failed,
    recover_stuck_items,
)
from app.data.repository.stock import (
    backfill_stock_basic_market,
    delete_unsupported_stock_data,
    upsert_stock_basic,
)
from app.data.repository.task_runs import (
    create_pending_task_run,
    get_active_task_run,
    get_latest_task_run,
    mark_stale_running_task_runs,
    mark_task_run_cancelled,
    mark_task_run_failed,
    mark_task_run_queue_failed,
    reconcile_task_run_status,
)

__all__ = [
    "create_alert",
    "list_alerts",
    "resolve_alert",
    "upsert_trade_calendar",
    "upsert_stock_fundamentals",
    "get_recent_fund_flow",
    "upsert_fund_flow",
    "get_active_stock_codes",
    "get_sync_progress",
    "upsert_daily_kline",
    "claim_kline_sync_items",
    "complete_job_if_done",
    "create_kline_sync_job",
    "get_job_progress",
    "insert_kline_sync_items",
    "list_job_items",
    "list_recent_jobs",
    "mark_item_done",
    "mark_item_failed",
    "recover_stuck_items",
    "backfill_stock_basic_market",
    "delete_unsupported_stock_data",
    "upsert_stock_basic",
    "create_pending_task_run",
    "get_active_task_run",
    "get_latest_task_run",
    "mark_stale_running_task_runs",
    "mark_task_run_cancelled",
    "mark_task_run_failed",
    "mark_task_run_queue_failed",
    "reconcile_task_run_status",
]
