from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, AsyncContextManager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.convert import _as_decimal
from app.data.fetcher import DataProvider, DataProviderError, default_providers, fetch_union, fetch_with_fallback, filter_open_circuits, get_data_proxy_url, stock_basic_providers
from app.data.models import DailyKline
from app.data.repository import (
    backfill_stock_basic_market,
    create_alert,
    delete_unsupported_stock_data,
    list_recent_jobs,
    upsert_daily_kline,
    upsert_stock_basic,
    upsert_trade_calendar,
)
from app.data.stock_scope import SUPPORTED_STOCK_SQL_CONDITION, is_supported_stock_basic, supported_stock_sql_condition
from app.data.validators import validate_daily_kline, validate_stock_basic, validate_trade_calendar

logger = logging.getLogger(__name__)

SAMPLE_STOCK_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sz_main", ("000", "001")),
    ("sz_sme", ("002",)),
    ("chinext", ("300", "301")),
    ("sh_main", ("600", "601")),
    ("sh_secondary", ("603", "605")),
)
PRICE_LIMIT_TOLERANCE = Decimal("0.0005")
MAIN_BOARD_PRICE_LIMIT = Decimal("0.10")
ST_PRICE_LIMIT = Decimal("0.05")






















from app.data.sample import (
    default_kline_window,
    _row_value,
    _sample_bucket,
    _balanced_sample_stock_codes,
    select_sample_stock_codes,
    select_all_stock_codes,
    get_data_status,
)

from app.data.quality import (
    _daily_kline_quality_issues,
    _create_kline_quality_alert,
    _bulk_load_is_st,
)

from app.data.window import (
    infer_incremental_kline_window,
    infer_incremental_kline_ranges,
    split_kline_ranges_by_year,
    infer_full_kline_ranges,
)


async def sync_stock_basic(
    session: AsyncSession,
    providers: list[DataProvider] | None = None,
) -> dict[str, Any]:
    provider_list = providers or stock_basic_providers()
    # P1 NEW-1: filter open-circuit providers BEFORE entering the worker thread.
    # fetch_with_fallback runs sync inside asyncio.to_thread and cannot call
    # the async breaker itself; doing it here lets failure_count actually
    # short-circuit a failing provider instead of burning max_retries each time.
    provider_list = await filter_open_circuits(session, provider_list, "stock_basic")
    if not provider_list:
        raise DataProviderError("all providers circuit-open for stock_basic")
    # End the transaction opened by filter_open_circuits BEFORE the long
    # network fetch below. The union walks adata + baostock + akshare
    # sequentially (~15-30s); if the session stayed idle-IN-transaction that
    # whole time, PostgreSQL's idle_in_transaction_session_timeout (30s) would
    # kill the connection and the later upsert would raise "connection is
    # closed". Rolling back leaves the connection idle (not in transaction),
    # which PG does not time out. (Guarded: test fakes lack rollback().)
    if hasattr(session, "rollback"):
        await session.rollback()
    # Union across ALL providers instead of stopping at the first non-empty
    # one: AData's all_code() returns only ~990 rows while AkShare returns the
    # full ~5900-row A-share universe. First-non-empty fallback shadowed the
    # complete list behind AData's truncated result (issue: 521 stocks).
    sources, records = await asyncio.wait_for(
        asyncio.to_thread(
            fetch_union,
            provider_list,
            "fetch_stock_basic",
            proxy_url=get_data_proxy_url(),
        ),
        # Bumped from 120s: union walks adata + baostock + akshare sequentially.
        timeout=300,
    )
    source = "+".join(sources)
    valid_records = []
    invalid_records = []
    excluded_records = []
    for record in records:
        try:
            validate_stock_basic(record)
        except Exception as exc:
            invalid_records.append({"ts_code": record.ts_code, "error": str(exc)})
            continue
        if not is_supported_stock_basic(record):
            excluded_records.append({"ts_code": record.ts_code, "market": record.market, "exchange": record.exchange})
            continue
        valid_records.append(record)

    if not valid_records:
        message = "stock basic sync returned no valid records"
        await create_alert(
            session,
            level="error",
            category="data_sync",
            title="Stock basic sync failed validation",
            message=message,
            payload={"invalid_records": invalid_records[:20]},
        )
        await session.commit()
        raise ValueError(message)

    count = await upsert_stock_basic(session, valid_records)
    await backfill_stock_basic_market(session)
    deleted = await delete_unsupported_stock_data(session)
    if invalid_records:
        await create_alert(
            session,
            level="warning",
            category="data_sync",
            title="Stock basic sync skipped invalid records",
            message=f"{len(invalid_records)} stock basic rows failed validation",
            payload={"invalid_records": invalid_records[:20]},
        )
    total = (await session.execute(text("SELECT COUNT(*) FROM stock_basic"))).scalar_one()
    await session.commit()
    return {
        "source": source,
        "inserted_or_updated": count,
        "total": total,
        "skipped": len(invalid_records) + len(excluded_records),
        "skipped_invalid": len(invalid_records),
        "skipped_excluded": len(excluded_records),
        "deleted_unsupported": deleted,
    }


async def sync_trade_calendar(
    session: AsyncSession,
    start_date: date,
    end_date: date,
    providers: list[DataProvider] | None = None,
) -> dict[str, Any]:
    provider_list = providers or default_providers()
    # P1 NEW-1: filter open-circuit providers in async land before to_thread.
    provider_list = await filter_open_circuits(session, provider_list, "trade_calendar")
    if not provider_list:
        raise DataProviderError("all providers circuit-open for trade_calendar")
    source, records = await asyncio.wait_for(
        asyncio.to_thread(
            fetch_with_fallback,
            provider_list,
            "fetch_trade_calendar",
            start_date,
            end_date,
            proxy_url=get_data_proxy_url(),
        ),
        timeout=120,
    )
    for record in records:
        validate_trade_calendar(record)

    count = await upsert_trade_calendar(session, records)
    latest = max((record.cal_date for record in records if record.is_open), default=None)
    await session.commit()
    return {"source": source, "inserted_or_updated": count, "start_date": start_date, "end_date": end_date}


async def sync_kline(
    session: AsyncSession | None,
    ts_codes: list[str] | None,
    start_date: date,
    end_date: date,
    providers: list[DataProvider] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    commit_each: bool = False,
    concurrency: int = 1,
    session_factory: Callable[[], AsyncContextManager[AsyncSession]] | None = None,
) -> dict[str, Any]:
    from app.db.session import async_session_factory as _per_stock_sf

    per_stock_session_factory = session_factory or _per_stock_sf
    provider_list = providers or default_providers()
    settings = get_settings()
    concurrency = max(1, concurrency)
    if ts_codes is None:
        assert session is not None, "session required when ts_codes is None"
        codes = await select_sample_stock_codes(session)
        if not codes:
            await sync_stock_basic(session, provider_list)
            codes = await select_sample_stock_codes(session)
    else:
        codes = ts_codes
    if not codes:
        raise ValueError("no stock codes available after stock basic sync; pass ts_codes explicitly")

    # ------------------------------------------------------------------
    # P1 NEW-1 (batch optimisation): one breaker query for ALL providers,
    # and one ST-status query for ALL ts_codes — BEFORE the per-stock loop.
    #
    # Previous implementation called filter_open_circuits + _is_st_stock
    # inside process_code, triggering 4000 × N_providers breaker queries
    # plus 4000 ST queries per sync_kline run. This collapses them to
    # exactly TWO queries regardless of stock count.
    # ------------------------------------------------------------------
    setup_session_cm = session  # use caller's session if available
    if setup_session_cm is None:
        # commit_each=True with session=None path — open a throwaway session
        # for the two upfront queries.
        async with per_stock_session_factory() as setup_session:
            filtered_provider_list = await filter_open_circuits(setup_session, provider_list, "daily_kline")
            is_st_by_code = await _bulk_load_is_st(setup_session, codes)
    else:
        filtered_provider_list = await filter_open_circuits(setup_session_cm, provider_list, "daily_kline")
        is_st_by_code = await _bulk_load_is_st(setup_session_cm, codes)

    if not filtered_provider_list:
        # All providers' circuits are open — install a sync stub that raises
        # before any HTTP call. fetch_with_fallback is sync (runs inside
        # asyncio.to_thread), so this stub must be sync too.
        def _no_provider_fetch(*_a, **_kw):
            raise DataProviderError("all providers circuit-open for daily_kline")
        fetch_to_use = _no_provider_fetch
        providers_for_each: list[DataProvider] = []
    else:
        fetch_to_use = fetch_with_fallback
        providers_for_each = filtered_provider_list

    total = 0
    completed = 0
    failures: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    progress_lock = asyncio.Lock()
    # PERF FIX: an unreachable/unauthenticated primary provider (e.g. AData
    # without a token) keeps failing per stock. The breaker only opens after
    # `circuit_breaker_threshold` failures, but the upfront filter_open_circuits
    # snapshot is taken once at task start, so within a single run the dead
    # provider would be retried for ALL stocks (each with backoff) until the
    # NEXT run. Re-checking every N stocks drops it mid-run instead.
    _recheck_interval = 50
    _since_recheck = 0

    async def _refilter_providers() -> None:
        nonlocal providers_for_each, _since_recheck
        _since_recheck += 1
        if _since_recheck < _recheck_interval:
            return
        _since_recheck = 0
        if not providers_for_each:
            return
        try:
            if setup_session_cm is not None:
                kept = await filter_open_circuits(setup_session_cm, providers_for_each, "daily_kline")
            else:
                async with per_stock_session_factory() as _rf_session:
                    kept = await filter_open_circuits(_rf_session, providers_for_each, "daily_kline")
            providers_for_each = kept
        except Exception:
            # Re-filter is best-effort; never let it break the sync.
            pass

    async def report_progress(ts_code: str) -> None:
        nonlocal completed
        if progress_callback is None:
            return
        await _refilter_providers()
        async with progress_lock:
            completed += 1
            try:
                progress_callback(completed, len(codes), ts_code)
            except Exception:
                pass

    async def process_code(ts_code: str) -> dict[str, Any]:
        if commit_each:
            async with per_stock_session_factory() as wk_session:
                try:
                    source, records = await asyncio.wait_for(
                        asyncio.to_thread(
                            fetch_to_use,
                            providers_for_each,
                            "fetch_daily_kline",
                            ts_code,
                            start_date,
                            end_date,
                            proxy_url=get_data_proxy_url(),
                        ),
                        timeout=settings.kline_per_stock_timeout_seconds,
                    )
                    for record in records:
                        validate_daily_kline(record)
                    quality_issues = _daily_kline_quality_issues(records, is_st=is_st_by_code.get(ts_code, False))
                    count = await upsert_daily_kline(wk_session, records)
                    latest = max((record.trade_date for record in records), default=None)
                    await _create_kline_quality_alert(
                        wk_session,
                        ts_code=ts_code,
                        source=source,
                        start_date=start_date,
                        end_date=end_date,
                        issues=quality_issues,
                    )
                    await wk_session.commit()
                    return {"ts_code": ts_code, "source": source, "count": count}
                except Exception as exc:
                    message = str(exc)
                    try:
                        await wk_session.rollback()
                    except Exception:
                        pass
                    await wk_session.commit()
                    return {"ts_code": ts_code, "error": message}

        try:
            source, records = await asyncio.wait_for(
                asyncio.to_thread(
                    fetch_to_use,
                    providers_for_each,
                    "fetch_daily_kline",
                    ts_code,
                    start_date,
                    end_date,
                    proxy_url=get_data_proxy_url(),
                ),
                timeout=settings.kline_per_stock_timeout_seconds,
            )
            for record in records:
                validate_daily_kline(record)
            assert session is not None
            quality_issues = _daily_kline_quality_issues(records, is_st=is_st_by_code.get(ts_code, False))
            count = await upsert_daily_kline(session, records)
            latest = max((record.trade_date for record in records), default=None)
            await _create_kline_quality_alert(
                session,
                ts_code=ts_code,
                source=source,
                start_date=start_date,
                end_date=end_date,
                issues=quality_issues,
            )
            return {"ts_code": ts_code, "source": source, "count": count}
        except Exception as exc:
            message = str(exc)
            assert session is not None
            return {"ts_code": ts_code, "error": message}

    if commit_each and concurrency > 1:
        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_process(ts_code: str) -> dict[str, Any]:
            async with semaphore:
                result = await process_code(ts_code)
                await report_progress(ts_code)
                return result

        results = await asyncio.gather(*(bounded_process(ts_code) for ts_code in codes))
    else:
        results = []
        for ts_code in codes:
            result = await process_code(ts_code)
            results.append(result)
            await report_progress(ts_code)

    for result in results:
        if "error" in result:
            failure = {"ts_code": result["ts_code"], "error": result["error"]}
            if result.get("skipped"):
                failure["skipped"] = True
            failures.append(failure)
            continue
        count = int(result["count"])
        total += count
        source = str(result["source"])
        source_counts[source] = source_counts.get(source, 0) + count

    if failures:
        # commit_each=True 时, 主 session 只在循环外做 batch 查询后就长时间空闲，
        # 循环内每只股票用的是 per_stock_session_factory() 新开的 session。
        # sync_sample_kline 处理 4000+ 股票可能耗时十几分钟，期间主 session 的
        # TCP 连接会被 PostgreSQL 服务端因 idle_in_transaction_session_timeout 或
        # keepalive 失败而关闭。等循环结束再用主 session 记录 alert 时就会报
        # asyncpg InterfaceError: connection is closed。
        # 修复: commit_each=True 时强制用新 session 记录 alert，与循环内股票
        # 处理的 session 生命周期对齐。
        if session is not None and not commit_each:
            await create_alert(
                session,
                level="warning" if total else "error",
                category="data_sync",
                title="Daily kline sync completed with failures",
                message=f"{len(failures)} symbols failed during kline sync",
                payload={
                    "start_date": start_date,
                    "end_date": end_date,
                    "failure_count": len(failures),
                    "failures": failures[:20],
                },
            )
            await session.commit()
        else:
            async with per_stock_session_factory() as alert_session:
                await create_alert(
                    alert_session,
                    level="warning" if total else "error",
                    category="data_sync",
                    title="Daily kline sync completed with failures",
                    message=f"{len(failures)} symbols failed during kline sync",
                    payload={
                        "start_date": start_date,
                        "end_date": end_date,
                        "failure_count": len(failures),
                        "failures": failures[:20],
                    },
                )
                await alert_session.commit()

    if not commit_each:
        assert session is not None
        await session.commit()

    # Distinguish "all skipped" (chronically failing stocks we deliberately
    # did not retry) from "all genuinely failed" (fetch errors). A batch where
    # every stock was skipped is NOT an error — the batch subtask will report
    # the skipped codes to kline_sync_failures so the completion gate can
    # drive them to permanent failure. Only raise when there are non-skipped
    # failures and zero successful inserts.
    skipped_codes = [f["ts_code"] for f in failures if f.get("skipped")]
    real_failures = [f for f in failures if not f.get("skipped")]
    if total == 0 and real_failures:
        raise RuntimeError(f"all kline sync attempts failed: {real_failures[0]['error']}")

    return {
        "requested_symbols": len(codes),
        "inserted_or_updated": total,
        "source_counts": source_counts,
        "failures": failures,
        "skipped_codes": skipped_codes,
    }


async def sync_one_stock(
    session_factory: Callable[[], AsyncContextManager[AsyncSession]],
    ts_code: str,
    start_date: date,
    end_date: date,
    providers: list[DataProvider] | None = None,
    per_stock_timeout: int | None = None,
) -> dict[str, Any]:
    """Sync daily K-line for a single stock using an independent DB session.

    Extracted from ``sync_kline``'s ``process_code`` (commit_each=True path)
    so per-stock sync can be dispatched individually — e.g. one Celery subtask
    per stock — without the batch orchestration overhead.

    Unlike ``sync_kline``:
    - Opens its own session via ``session_factory()`` (no shared session).
    - Filters open-circuit providers and loads ST status for just this one
      ts_code inside the per-stock session.
    - Always commits (success or failure) before returning.

    Returns ``{"success": bool, "error": str | None, "source": str | None,
    "synced": int}``.
    """
    from app.db.session import async_session_factory as _default_sf

    sf = session_factory or _default_sf
    provider_list = providers or default_providers()
    settings = get_settings()
    timeout_seconds = per_stock_timeout or settings.kline_per_stock_timeout_seconds

    async with sf() as session:
        try:
            filtered_providers = await filter_open_circuits(session, provider_list, "daily_kline")
            if not filtered_providers:
                raise DataProviderError("all providers circuit-open for daily_kline")

            is_st_map = await _bulk_load_is_st(session, [ts_code])
            is_st = is_st_map.get(ts_code, False)

            # End the transaction opened by the breaker/ST queries BEFORE the
            # long network fetch below, to avoid PostgreSQL's
            # idle_in_transaction_session_timeout killing the connection.
            if hasattr(session, "rollback"):
                await session.rollback()

            async with asyncio.timeout(timeout_seconds):
                source, records = await asyncio.to_thread(
                    fetch_with_fallback,
                    filtered_providers,
                    "fetch_daily_kline",
                    ts_code,
                    start_date,
                    end_date,
                    proxy_url=get_data_proxy_url(),
                )

            for record in records:
                validate_daily_kline(record)

            quality_issues = _daily_kline_quality_issues(records, is_st=is_st)
            count = await upsert_daily_kline(session, records)
            latest = max((record.trade_date for record in records), default=None)
            await _create_kline_quality_alert(
                session,
                ts_code=ts_code,
                source=source,
                start_date=start_date,
                end_date=end_date,
                issues=quality_issues,
            )
            await session.commit()
            return {"success": True, "error": None, "source": source, "synced": count}
        except TimeoutError as exc:
            # asyncio.timeout raises TimeoutError with an EMPTY message by default.
            # Give it a meaningful message so last_error / alerts are debuggable.
            message = str(exc) or f"fetch_with_fallback timed out after {timeout_seconds}s"
            try:
                if hasattr(session, "rollback"):
                    await session.rollback()
            except Exception:
                pass
            await session.commit()
            return {"success": False, "error": message, "source": None, "synced": 0}
        except Exception as exc:
            message = str(exc)
            try:
                if hasattr(session, "rollback"):
                    await session.rollback()
            except Exception:
                pass
            await session.commit()
            return {"success": False, "error": message, "source": None, "synced": 0}








