from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.core.asyncio_runtime import run_async
from app.core.config import get_settings
from app.data.fetcher import (
    DataProvider,
    DataProviderError,
    default_providers,
    filter_open_circuits,
    get_data_proxy_url,
    ping_providers,
    providers_for_method,
)
from app.data.repository import (
    claim_kline_sync_items,
    complete_job_if_done,
    create_alert,
    create_kline_sync_job,
    get_job_progress,
    insert_kline_sync_items,
    mark_item_done,
    mark_item_failed,
    mark_stale_running_task_runs,
    recover_stuck_items,
)
from app.data.service import (
    default_kline_window,
    infer_full_kline_ranges,
    infer_incremental_kline_ranges,
    select_all_stock_codes,
    split_kline_ranges_by_year,
    sync_kline,
    sync_one_stock,
    sync_stock_basic,
    sync_trade_calendar,
)
from app.backtest.kline_cache import invalidate_all_kline_cache
from app.data.stock_service import sync_fundamentals
from app.db.session import async_session_factory
from app.tasks.beat_lock import with_beat_lock
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked, with_session

logger = logging.getLogger(__name__)


def _effective_data_sync_concurrency(concurrency: int | None) -> int:
    effective_concurrency = concurrency if concurrency is not None else get_settings().full_kline_sync_concurrency
    if not 1 <= effective_concurrency <= 16:
        raise ValueError("concurrency must be between 1 and 16")
    return effective_concurrency


# ---------------------------------------------------------------------------
# K-line sync — DB queue architecture (dispatch → worker → recover)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.data_tasks.kline_sync_dispatch",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
@with_beat_lock("app.tasks.data_tasks.kline_sync_dispatch")
def kline_sync_dispatch(
    self,
    *,
    job_type: str = "incremental",
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Create a kline_sync_jobs row, compute ranges, insert items, start workers."""
    settings = get_settings()
    config: dict[str, Any] = {
        "ts_codes": ts_codes,
        "start_date": start_date,
        "end_date": end_date,
    }

    override_start = date.fromisoformat(start_date) if start_date else None
    override_end = date.fromisoformat(end_date) if end_date else None

    async def run(session_factory) -> dict[str, Any]:
        # --- Provider health check (independent session) ---
        # Test each provider with a single stock before creating the job.
        # Uses a separate session to avoid transaction pollution.
        kline_providers = providers_for_method("fetch_daily_kline")
        async with session_factory() as ping_session:
            alive = await ping_providers(ping_session, kline_providers, "daily_kline")
            try:
                await ping_session.commit()
            except Exception:
                logger.warning("silent except in run", exc_info=True)
                await ping_session.rollback()
        if not alive:
            logger.warning(
                "kline_sync_dispatch: all providers failed ping — proceeding with unfiltered list; "
                "per-stock fallback will handle failures"
            )

        # --- Main sync flow (independent session) ---
        async with session_factory() as session:
            job_id = await create_kline_sync_job(session, job_type=job_type, config=config)

            if job_type == "full":
                ranges = await infer_full_kline_ranges(session, ts_codes=ts_codes, limit=settings.kline_sync_test_limit)
            else:
                ranges = await infer_incremental_kline_ranges(session, ts_codes=ts_codes, limit=settings.kline_sync_test_limit)

            if override_start is not None:
                for item in ranges:
                    if item["start_date"] < override_start:
                        item["start_date"] = override_start
            if override_end is not None:
                for item in ranges:
                    if item["end_date"] > override_end:
                        item["end_date"] = override_end

            ranges = split_kline_ranges_by_year(ranges)

            items = [
                {
                    "ts_code": r["ts_code"],
                    "start_date": r["start_date"],
                    "end_date": r["end_date"],
                }
                for r in ranges
            ]

            await insert_kline_sync_items(session, job_id=job_id, items=items)

        if not items:
            try:
                self.update_state(
                    state="PROGRESS",
                    meta={"done": 0, "total": 0, "running": 0, "pending": 0},
                )
            except Exception:
                logger.debug("silent except in run")
                pass
            return {
                "job_id": job_id,
                "scope_total": 0,
                "workers": 0,
                "skipped": True,
                "reason": "no per-stock kline gaps found",
            }

        try:
            self.update_state(
                state="PROGRESS",
                meta={"done": 0, "total": len(items), "running": 0, "pending": len(items)},
            )
        except Exception:
            logger.debug("silent except in run")
            pass

        for _ in range(settings.kline_sync_worker_count):
            kline_sync_worker.apply_async(kwargs={"job_id": job_id})

        return {
            "_task_status": "dispatched",
            "job_id": job_id,
            "scope_total": len(items),
            "workers": settings.kline_sync_worker_count,
        }

    return run_async(
        _run_tracked(
            "kline_sync_dispatch",
            self.request.id,
            {"job_type": job_type, "ts_codes": ts_codes, "start_date": start_date, "end_date": end_date},
            run,
        )
    )


@celery_app.task(
    name="app.tasks.data_tasks.kline_sync_worker",
    bind=True,
    max_retries=0,
    soft_time_limit=get_settings().celery_task_soft_time_limit,
    time_limit=get_settings().celery_task_time_limit,
)
def kline_sync_worker(self, *, job_id: int) -> dict[str, Any]:
    """Pull items from DB queue, process stocks, update status."""
    worker_id = self.request.id
    settings = get_settings()
    budget = settings.kline_sync_worker_budget_seconds
    concurrency = settings.kline_sync_worker_concurrency
    max_attempts = settings.kline_sync_max_attempts
    # Per-round hard timeout: per_stock_timeout + 30s grace for DB ops.
    # Prevents a single hung asyncio.gather from blocking the worker forever.
    round_timeout = settings.kline_per_stock_timeout_seconds + 30

    async def run() -> dict[str, Any]:
        start = time.monotonic()
        processed = 0
        progress_counter = 0

        while time.monotonic() - start < budget:
            async with async_session_factory() as session:
                items = await claim_kline_sync_items(
                    session, job_id=job_id, count=concurrency, worker_id=worker_id,
                )

            if not items:
                break

            sem = asyncio.Semaphore(concurrency)

            async def process_item(item: dict) -> None:
                nonlocal processed
                async with sem:
                    result = await sync_one_stock(
                        async_session_factory,
                        item["ts_code"],
                        item["start_date"],
                        item["end_date"],
                    )
                    async with async_session_factory() as session:
                        if result["success"]:
                            await mark_item_done(session, item_id=item["id"], job_id=job_id)
                        else:
                            is_permanent = await mark_item_failed(
                                session,
                                item_id=item["id"],
                                job_id=job_id,
                                error=result["error"] or "",
                                max_attempts=max_attempts,
                            )
                            if is_permanent:
                                await create_alert(
                                    session,
                                    level="warning",
                                    category="data_sync",
                                    title="K线同步永久失败",
                                    message=(
                                        f"{item['ts_code']} 连续失败 {max_attempts} 次,"
                                        f"已标记永久失败,不再自动重投"
                                    ),
                                    payload={
                                        "job_id": job_id,
                                        "ts_code": item["ts_code"],
                                        "max_attempts": max_attempts,
                                    },
                                )
                                await session.commit()

                processed += 1

            try:
                await asyncio.wait_for(
                    asyncio.gather(*(process_item(item) for item in items)),
                    timeout=round_timeout,
                )
            except TimeoutError:
                # The gather hung — likely due to a stuck to_thread that
                # asyncio.timeout couldn't cancel. Items left in 'running'
                # will be recovered by kline_sync_recover_stuck. Break out
                # so the worker can complete/requeue instead of hanging forever.
                logger.warning(
                    "kline_sync_worker round timed out after %ss (job=%d, items=%d) — "
                    "stuck items will be recovered by recover_stuck",
                    round_timeout, job_id, len(items),
                )
                break

            progress_counter += len(items)
            if progress_counter >= 50:
                progress_counter = 0
                try:
                    async with async_session_factory() as session:
                        progress = await get_job_progress(session, job_id=job_id)
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "done": progress["scope_done"],
                            "total": progress["scope_total"],
                            "running": progress["running"],
                            "pending": progress["pending"],
                        },
                    )
                except Exception:
                    logger.debug("silent except in run")
                    pass

        async with async_session_factory() as session:
            completed = await complete_job_if_done(session, job_id=job_id)
            if completed:
                # Whole K-line sync job finished — backtest cache may now be
                # stale for the synced pool, so wipe it. Invalidation is
                # prefix-based (safe: backtest cache is only read during
                # backtests and repopulates on next run).
                await invalidate_all_kline_cache()
            else:
                progress = await get_job_progress(session, job_id=job_id)
                # Re-launch is handled by kline_sync_recover_stuck (controller
                # pattern) which runs every 60s — no self-re-launch here to
                # avoid worker cascade races.

        return {"job_id": job_id, "processed": processed, "completed": completed}

    return run_async(run())


@celery_app.task(
    name="app.tasks.data_tasks.kline_sync_recover_stuck",
    bind=False,
    max_retries=0,
)
@with_beat_lock("app.tasks.data_tasks.kline_sync_recover_stuck")
def kline_sync_recover_stuck() -> dict[str, Any]:
    """Reset stuck running items AND re-launch workers for jobs with pending items.

    Controller pattern: instead of each worker re-launching itself (which creates
    a cascade race condition), this periodic task checks for jobs that still have
    pending items and launches new workers. Runs every 60s via Celery beat.
    """
    settings = get_settings()

    async def run() -> dict[str, Any]:
        async with async_session_factory() as session:
            recovered = await recover_stuck_items(
                session, stuck_seconds=settings.kline_sync_stuck_seconds,
            )

            # Re-launch: find running jobs with pending items, start a worker.
            launched = 0
            result = await session.execute(
                text(
                    """
                    SELECT j.id
                    FROM kline_sync_jobs j
                    WHERE j.status = 'running'
                      AND EXISTS (
                          SELECT 1 FROM kline_sync_items i
                          WHERE i.job_id = j.id AND i.status = 'pending'
                      )
                    ORDER BY j.id
                    """
                ),
            )
            for row in result.all():
                job_id = int(row[0])
                kline_sync_worker.apply_async(kwargs={"job_id": job_id})
                launched += 1

            return {"recovered": recovered, "launched": launched, "stuck_seconds": settings.kline_sync_stuck_seconds}

    return run_async(run())


# ---------------------------------------------------------------------------
# Non-K-line data tasks (kept from original)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.data_tasks.update_stock_basic",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
@with_beat_lock("app.tasks.data_tasks.update_stock_basic")
def update_stock_basic(self) -> dict[str, Any]:
    return run_async(
        _run_tracked(
            "update_stock_basic",
            self.request.id,
            {},
            with_session(sync_stock_basic),
        )
    )


@celery_app.task(
    name="app.tasks.data_tasks.update_trade_calendar",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
@with_beat_lock("app.tasks.data_tasks.update_trade_calendar")
def update_trade_calendar(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    today = datetime.now(tz=UTC).date()
    start = date.fromisoformat(start_date) if start_date else today - timedelta(days=370)
    end = date.fromisoformat(end_date) if end_date else today + timedelta(days=40)
    return run_async(
        _run_tracked(
            "update_trade_calendar",
            self.request.id,
            {"start_date": start, "end_date": end},
            with_session(sync_trade_calendar, start, end),
        )
    )


@celery_app.task(
    name="app.tasks.data_tasks.sync_sample_kline",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
def sync_sample_kline(
    self,
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_kline_window()
    start = date.fromisoformat(start_date) if start_date else default_start
    end = date.fromisoformat(end_date) if end_date else default_end
    effective_concurrency = _effective_data_sync_concurrency(concurrency)
    return run_async(
        _run_tracked(
            "sync_sample_kline",
            self.request.id,
            {"ts_codes": ts_codes, "start_date": start, "end_date": end, "concurrency": effective_concurrency},
            with_session(
                sync_kline,
                ts_codes,
                start,
                end,
                commit_each=True,
                concurrency=effective_concurrency,
            ),
        )
    )


@celery_app.task(
    name="app.tasks.data_tasks.sync_fundamentals",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
@with_beat_lock("app.tasks.data_tasks.sync_fundamentals")
def sync_fundamentals_task(
    self,
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_kline_window()
    start = date.fromisoformat(start_date) if start_date else default_start
    end = date.fromisoformat(end_date) if end_date else default_end
    effective_concurrency = _effective_data_sync_concurrency(concurrency)
    payload = {"ts_codes": ts_codes, "start_date": start, "end_date": end, "concurrency": effective_concurrency}

    async def run(session_factory) -> dict[str, Any]:
        async with session_factory() as session:
            all_codes = [*ts_codes] if ts_codes is not None else await select_all_stock_codes(session)
        # session closed by context manager; remaining work uses no session.
        total = len(all_codes)

        def progress(i: int, _total: int, code: str) -> None:
            try:
                self.update_state(
                    state="PROGRESS",
                    meta={"current": i, "total": total, "current_code": code},
                )
            except Exception:
                logger.debug("silent except in progress")
                pass

        return await sync_fundamentals(
            None,
            all_codes,
            start,
            end,
            progress_callback=progress,
            commit_each=True,
            concurrency=effective_concurrency,
        )

    return run_async(
        _run_tracked(
            "sync_fundamentals",
            self.request.id,
            payload,
            run,
        )
    )


@celery_app.task(
    name="app.tasks.data_tasks.cleanup_stale_task_runs",
    bind=False,
    max_retries=0,
)
@with_beat_lock("app.tasks.data_tasks.cleanup_stale_task_runs")
def cleanup_stale_task_runs() -> dict[str, Any]:
    """Periodically mark zombie task_runs (status=running, stale) as failed.

    Runs every hour via Celery beat. Reuses repository.mark_stale_running_task_runs.
    Stale threshold is configurable via STALE_TASK_RUN_HOURS (default: 2h).
    """
    stale_hours = get_settings().stale_task_run_hours

    async def run() -> int:
        async with async_session_factory() as session:
            return await mark_stale_running_task_runs(
                session,
                older_than=timedelta(hours=stale_hours),
                error_message="stale running task after periodic cleanup",
            )

    cleaned = run_async(run())
    if cleaned:
        logger.warning(
            "cleanup_stale_task_runs marked %s stale record(s) as failed (threshold=%dh)",
            cleaned,
            stale_hours,
        )
    return {"cleaned": cleaned, "stale_hours": stale_hours}
