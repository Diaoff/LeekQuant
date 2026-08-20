from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fetcher import (
    DataProvider,
    DataProviderError,
    default_providers,
    fetch_with_fallback,
    filter_open_circuits,
    get_data_proxy_url,
)
from app.data.models import FundFlowDaily
from app.data.repository import upsert_fund_flow
from app.data.stock_service import normalize_ts_code
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def sync_fund_flow(
    session: AsyncSession | None,
    ts_codes: list[str] | None,
    target_date: date,
    providers: list[DataProvider] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    commit_each: bool = False,
    concurrency: int = 1,
) -> dict[str, Any]:
    from app.data.service import select_all_stock_codes

    provider_list = providers or default_providers()
    concurrency = max(1, concurrency)
    if ts_codes is None:
        assert session is not None, "session required when ts_codes is None"
        codes = await select_all_stock_codes(session)
    else:
        codes = [normalize_ts_code(code) for code in ts_codes]
    if not codes:
        raise ValueError("no stock codes available for fund_flow sync")

    # AkShare 的 stock_individual_fund_flow 仅返回最近约 120 个交易日（截至最新
    # 可用日），且不支持按日期增量。若 start=end=target_date(今天)，今天数据未发布
    # 时过滤后 0 行会被 fetch_with_fallback 判为"无数据"→ 任务误失败。改为按滚动窗口
    # 拉取（start=target_date-ROLLING_DAYS），由 upsert 去重，保证表始终刷新到最新
    # 可用日，且不会因当日数据滞后而误报失败。
    ROLLING_DAYS = 250
    start_date = target_date - timedelta(days=ROLLING_DAYS)
    end_date = target_date

    total = len(codes)
    completed = 0
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    progress_lock = asyncio.Lock()

    async def report_progress(ts_code: str) -> None:
        nonlocal completed
        if progress_callback is None:
            return
        async with progress_lock:
            completed += 1
            try:
                progress_callback(completed, total, ts_code)
            except Exception:
                logger.debug("silent except in report_progress")
                pass

    async def process_code(code: str) -> dict[str, Any]:
        try:
            if commit_each:
                async with async_session_factory() as wk_session:
                    try:
                        per_stock_providers = await filter_open_circuits(
                            wk_session, provider_list, "fund_flow"
                        )
                        if not per_stock_providers:
                            raise DataProviderError(
                                f"all providers circuit-open for fund_flow ({code})"
                            )
                        source, records = await asyncio.to_thread(
                            fetch_with_fallback,
                            per_stock_providers,
                            "fetch_fund_flow",
                            [code],
                            start_date,
                            end_date,
                            proxy_url=get_data_proxy_url(),
                        )
                        count = await upsert_fund_flow(wk_session, records)
                        await wk_session.commit()
                        source_counts[source] = source_counts.get(source, 0) + 1
                        return {"ts_code": code, "source": source, "count": count}
                    except Exception as exc:
                        logger.warning("silent except in process_code (exc)", exc_info=True)
                        message = str(exc)
                        try:
                            await wk_session.rollback()
                        except Exception:
                            logger.debug("silent except in process_code rollback")
                        await wk_session.commit()
                        return {"ts_code": code, "error": message}

            filtered_provider_list = await filter_open_circuits(
                session, provider_list, "fund_flow"
            )
            if not filtered_provider_list:
                raise DataProviderError(
                    f"all providers circuit-open for fund_flow ({code})"
                )
            source, records = await asyncio.to_thread(
                fetch_with_fallback,
                filtered_provider_list,
                "fetch_fund_flow",
                [code],
                start_date,
                end_date,
                proxy_url=get_data_proxy_url(),
            )
            assert session is not None
            count = await upsert_fund_flow(session, records)
            source_counts[source] = source_counts.get(source, 0) + 1
            return {"ts_code": code, "source": source, "count": count}
        except Exception as exc:
            logger.warning("silent except in process_code (exc)", exc_info=True)
            message = str(exc)
            return {"ts_code": code, "error": message}

    if commit_each and concurrency > 1:
        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_process(code: str) -> dict[str, Any]:
            async with semaphore:
                result = await process_code(code)
                await report_progress(code)
                return result

        results = await asyncio.gather(*(bounded_process(code) for code in codes))
    else:
        results = []
        for code in codes:
            result = await process_code(code)
            results.append(result)
            await report_progress(code)

    for r in results:
        if "error" in r:
            failures.append(r)
        else:
            successes.append(r)

    return {
        "target_date": target_date.isoformat(),
        "total": total,
        "successes": len(successes),
        "failures": len(failures),
        "source_counts": source_counts,
        "failures_detail": failures[:50],
    }
