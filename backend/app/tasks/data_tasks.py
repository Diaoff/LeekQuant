from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.data.providers import DataProviderError
from app.data.service import (
    default_kline_window,
    infer_incremental_kline_ranges,
    select_all_stock_codes,
    sync_kline,
    sync_stock_basic,
    sync_trade_calendar,
)
from app.data.stock_service import sync_fundamentals
from app.tasks.beat_lock import with_beat_lock
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked, with_session


def _effective_data_sync_concurrency(concurrency: int | None) -> int:
    effective_concurrency = concurrency if concurrency is not None else get_settings().full_kline_sync_concurrency
    if not 1 <= effective_concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    return effective_concurrency


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
    return asyncio.run(
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
    return asyncio.run(
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
    return asyncio.run(
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

    return asyncio.run(
        _run_tracked(
            "sync_fundamentals",
            self.request.id,
            payload,
            run,
        )
    )


@celery_app.task(
    name="app.tasks.data_tasks.incremental_kline_update",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
@with_beat_lock("app.tasks.data_tasks.incremental_kline_update")
def incremental_kline_update(self, concurrency: int | None = None) -> dict[str, Any]:
    effective_concurrency = _effective_data_sync_concurrency(concurrency)

    async def run(session_factory) -> dict[str, Any]:
        async with session_factory() as session:
            ranges = await infer_incremental_kline_ranges(session)
            all_codes = await select_all_stock_codes(session)
        # session closed by context manager; remaining work uses no session.
        if not ranges:
            return {
                "skipped": True,
                "reason": "no per-stock kline gaps found",
                "requested_symbols": 0,
                "gap_symbols": 0,
                "skipped_symbols": len(all_codes),
                "inserted_or_updated": 0,
                "source_counts": {},
                "failures": [],
            }

        grouped_ranges: dict[tuple[date, date], list[str]] = {}
        for item in ranges:
            key = (item["start_date"], item["end_date"])
            grouped_ranges.setdefault(key, []).append(item["ts_code"])

        total = len(ranges)
        progress_offset = 0

        def progress(i: int, _total: int, code: str) -> None:
            try:
                self.update_state(
                    state="PROGRESS",
                    meta={"current": progress_offset + i, "total": total, "current_code": code},
                )
            except Exception:
                pass

        inserted_or_updated = 0
        source_counts: dict[str, int] = {}
        failures: list[dict[str, str]] = []

        for (start, end), ts_codes in grouped_ranges.items():
            result = await sync_kline(
                None,
                ts_codes,
                start,
                end,
                progress_callback=progress,
                commit_each=True,
                concurrency=effective_concurrency,
            )
            progress_offset += len(ts_codes)
            inserted_or_updated += int(result.get("inserted_or_updated", 0))
            for source, count in result.get("source_counts", {}).items():
                source_counts[source] = source_counts.get(source, 0) + int(count)
            failures.extend(result.get("failures", []))

        return {
            "requested_symbols": total,
            "gap_symbols": total,
            "skipped_symbols": max(len(all_codes) - total, 0),
            "inserted_or_updated": inserted_or_updated,
            "source_counts": source_counts,
            "failures": failures,
        }

    return asyncio.run(
        _run_tracked(
            "incremental_kline_update",
            self.request.id,
            {"concurrency": effective_concurrency},
            run,
        )
    )


@celery_app.task(
    name="app.tasks.data_tasks.sync_all_kline",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
def sync_all_kline(
    self,
    start_date: str | None = None,
    end_date: str | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_kline_window()
    start = date.fromisoformat(start_date) if start_date else default_start
    end = date.fromisoformat(end_date) if end_date else default_end
    effective_concurrency = _effective_data_sync_concurrency(concurrency)

    async def run(session_factory) -> dict[str, Any]:
        async with session_factory() as session:
            all_codes = await select_all_stock_codes(session)
        # session closed by context manager; remaining work uses no session.
        total = len(all_codes)

        def progress(i: int, _total: int, code: str) -> None:
            try:
                self.update_state(
                    state="PROGRESS",
                    meta={"current": i, "total": total, "current_code": code},
                )
            except Exception:
                pass

        return await sync_kline(
            None,
            all_codes,
            start,
            end,
            progress_callback=progress,
            commit_each=True,
            concurrency=effective_concurrency,
        )

    return asyncio.run(
        _run_tracked(
            "sync_all_kline",
            self.request.id,
            {"start_date": start, "end_date": end, "concurrency": effective_concurrency},
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
    from app.data.repository import mark_stale_running_task_runs
    from app.db.session import async_session_factory

    stale_hours = get_settings().stale_task_run_hours

    async def run() -> int:
        async with async_session_factory() as session:
            return await mark_stale_running_task_runs(
                session,
                older_than=timedelta(hours=stale_hours),
                error_message="stale running task after periodic cleanup",
            )

    cleaned = asyncio.run(run())
    if cleaned:
        logging.getLogger(__name__).warning(
            "cleanup_stale_task_runs marked %s stale record(s) as failed (threshold=%dh)",
            cleaned,
            stale_hours,
        )
    return {"cleaned": cleaned, "stale_hours": stale_hours}
