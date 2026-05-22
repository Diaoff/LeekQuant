from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from app.data.service import (
    default_kline_window,
    infer_incremental_kline_window,
    select_all_stock_codes,
    sync_kline,
    sync_stock_basic,
    sync_trade_calendar,
)
from app.data.stock_service import sync_fundamentals
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked


@celery_app.task(name="app.tasks.data_tasks.update_stock_basic", bind=True)
def update_stock_basic(self) -> dict[str, Any]:
    return asyncio.run(
        _run_tracked(
            "update_stock_basic",
            self.request.id,
            {},
            lambda session: sync_stock_basic(session),
        )
    )


@celery_app.task(name="app.tasks.data_tasks.update_trade_calendar", bind=True)
def update_trade_calendar(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    today = datetime.now(tz=UTC).date()
    start = date.fromisoformat(start_date) if start_date else today - timedelta(days=370)
    end = date.fromisoformat(end_date) if end_date else today + timedelta(days=40)
    return asyncio.run(
        _run_tracked(
            "update_trade_calendar",
            self.request.id,
            {"start_date": start, "end_date": end},
            lambda session: sync_trade_calendar(session, start, end),
        )
    )


@celery_app.task(name="app.tasks.data_tasks.sync_sample_kline", bind=True)
def sync_sample_kline(
    self,
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_kline_window()
    start = date.fromisoformat(start_date) if start_date else default_start
    end = date.fromisoformat(end_date) if end_date else default_end
    return asyncio.run(
        _run_tracked(
            "sync_sample_kline",
            self.request.id,
            {"ts_codes": ts_codes, "start_date": start, "end_date": end},
            lambda session: sync_kline(session, ts_codes, start, end),
        )
    )


@celery_app.task(name="app.tasks.data_tasks.sync_fundamentals", bind=True)
def sync_fundamentals_task(
    self,
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_kline_window()
    start = date.fromisoformat(start_date) if start_date else default_start
    end = date.fromisoformat(end_date) if end_date else default_end
    return asyncio.run(
        _run_tracked(
            "sync_fundamentals",
            self.request.id,
            {"ts_codes": ts_codes, "start_date": start, "end_date": end},
            lambda session: sync_fundamentals(session, ts_codes, start, end),
        )
    )


@celery_app.task(name="app.tasks.data_tasks.incremental_kline_update", bind=True)
def incremental_kline_update(self) -> dict[str, Any]:
    async def run(session) -> dict[str, Any]:
        start, end = await infer_incremental_kline_window(session)
        if start is None or end is None:
            return {"skipped": True, "reason": "no new open trade dates"}
        all_codes = await select_all_stock_codes(session)
        return await sync_kline(session, all_codes, start, end)

    return asyncio.run(
        _run_tracked(
            "incremental_kline_update",
            self.request.id,
            {},
            run,
        )
    )


@celery_app.task(name="app.tasks.data_tasks.sync_all_kline", bind=True)
def sync_all_kline(
    self,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_kline_window()
    start = date.fromisoformat(start_date) if start_date else default_start
    end = date.fromisoformat(end_date) if end_date else default_end

    async def run(session) -> dict[str, Any]:
        all_codes = await select_all_stock_codes(session)
        await session.close()
        total = len(all_codes)

        def progress(i: int, _total: int, code: str) -> None:
            if i != 1 and i != total and i % 10 != 0:
                return
            try:
                self.update_state(
                    state="PROGRESS",
                    meta={"current": i, "total": total, "current_code": code},
                )
            except Exception:
                pass

        return await sync_kline(None, all_codes, start, end, progress_callback=progress, commit_each=True)

    return asyncio.run(
        _run_tracked(
            "sync_all_kline",
            self.request.id,
            {"start_date": start, "end_date": end},
            run,
        )
    )
