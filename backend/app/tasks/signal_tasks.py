"""Celery tasks for daily strategy signal generation."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.backtest.adapter import BacktestContext, KBar
from app.backtest.strategy_runtime import StrategyExecutionResult, execute_strategy
from app.data.stock_scope import supported_stock_sql_condition
from app.data.providers import DataProviderError
from app.preferences.service import get_full_kline_sync_concurrency
from app.realtime.models import RealtimeTick
from app.realtime.providers import EastMoneyRealtimeProvider
from app.sim.service import SignalOrderRequest, _insert_signal, _money, generate_order_from_signal
from app.tasks.beat_lock import with_beat_lock
from app.tasks.celery_app import celery_app
from app.tasks.tracking import _run_tracked, with_session
from app.core.asyncio_runtime import run_async
from app.core.convert import _dec
import logging
logger = logging.getLogger(__name__)

LOOKBACK_BARS = 60
MAX_SIGNAL_CONCURRENCY = 4
BUY_SIGNAL_TYPES = {"买入", "增持"}
EXIT_SIGNAL_TYPES = {"卖出", "减仓"}
INTRADAY_POSITION_SIGNAL_TYPES = {"增持", "减仓", "卖出", "观望"}
A_SHARE_INTRADAY_WINDOWS = (
    (time(9, 25), time(11, 30)),
    (time(13, 0), time(15, 0)),
)

SIGNAL_EVENTS_CHANNEL = "signal:new"


class StrategySignalExecutionError(RuntimeError):
    def __init__(self, result: StrategyExecutionResult):
        super().__init__(result.error_summary())
        self.result = result


def _parse_kbar(row: dict[str, Any]) -> KBar:
    return KBar(
        ts_code=row["ts_code"],
        trade_date=row["trade_date"] if isinstance(row["trade_date"], date) else date.fromisoformat(str(row["trade_date"])),
        open=_dec(row.get("open")),
        high=_dec(row.get("high")),
        low=_dec(row.get("low")),
        close=_dec(row.get("close")),
        pre_close=_dec(row.get("pre_close")),
        volume=int(row.get("volume") or 0),
        amount=_dec(row.get("amount")),
        turnover_rate=_dec(row.get("turnover_rate")) if row.get("turnover_rate") is not None else None,
        adj_factor=_dec(row.get("adj_factor")) if row.get("adj_factor") is not None else None,
        is_suspended=bool(row.get("is_suspended", False)),
        is_limit_up=bool(row.get("is_limit_up", False)),
        is_limit_down=bool(row.get("is_limit_down", False)),
    )


def _exec_strategy(source_code: str, ctx: BacktestContext) -> StrategyExecutionResult:
    return execute_strategy(source_code, ctx)


def _is_intraday_trading_time(now: datetime | None = None) -> bool:
    current = now or datetime.now()
    current_time = current.time()
    return any(start <= current_time <= end for start, end in A_SHARE_INTRADAY_WINDOWS)


async def _is_open_trade_day(session, run_date: date) -> bool:
    result = await session.execute(
        text("SELECT is_open FROM trade_calendar WHERE cal_date = :run_date"),
        {"run_date": run_date},
    )
    row = result.mappings().one_or_none()
    return bool(row and row["is_open"])


async def _fetch_midday_quotes(stock_codes: list[str]) -> dict[str, Decimal]:
    if not stock_codes:
        return {}
    try:
        from app.data.providers import TencentHttpProvider, AkShareProvider
    except ImportError:
        logger.debug("silent except in _fetch_midday_quotes")
        return {}
    for provider_cls, name in [(TencentHttpProvider, "tencent"), (AkShareProvider, "akshare")]:
        try:
            provider = provider_cls()
            result = await asyncio.to_thread(provider.fetch_realtime_quote, stock_codes)
            if result:
                return result
        except Exception:
            logger.debug("silent except in _fetch_midday_quotes")
            continue
    return {}


async def _resolve_signal_trade_date(session, requested: date | None) -> date | None:
    if requested is not None:
        return requested
    result = await session.execute(
        text(
            """
            SELECT MAX(trade_date)
            FROM daily_kline
            """
        )
    )
    latest_kline_date = result.scalar_one_or_none()
    if latest_kline_date is not None:
        return latest_kline_date

    calendar_result = await session.execute(
        text(
            """
            SELECT cal_date
            FROM trade_calendar
            WHERE is_open = TRUE AND cal_date <= CURRENT_DATE
            ORDER BY cal_date DESC
            LIMIT 1
            """
        )
    )
    return calendar_result.scalar_one_or_none()


async def _active_strategies(session) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, user_id, name, source_code
            FROM strategies
            WHERE status = 'active'
            ORDER BY id
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def _stock_codes(session) -> list[str]:
    result = await session.execute(
        text(
            """
            WITH latest_fund AS (
                SELECT DISTINCT ON (ts_code) ts_code, pe_ttm
                FROM stock_fundamentals
                WHERE report_date IS NOT NULL
                ORDER BY ts_code, report_date DESC
            )
            SELECT sb.ts_code
            FROM stock_basic sb
            LEFT JOIN latest_fund f ON f.ts_code = sb.ts_code
            WHERE sb.is_delisted = FALSE
              AND sb.is_st = FALSE
              AND (f.pe_ttm IS NULL OR f.pe_ttm > 0)
              AND """ + supported_stock_sql_condition("sb") + """
            ORDER BY sb.symbol
            """
        )
    )
    return [row["ts_code"] for row in result.mappings().all()]


async def _bound_accounts(session, strategy_id: int) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, user_id
            FROM sim_accounts
            WHERE status = 'active' AND strategy_id = :strategy_id
            ORDER BY id
            """
        ),
        {"strategy_id": strategy_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def _active_accounts_with_strategies(session) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT a.id, a.user_id, a.total_asset, a.strategy_id,
                   s.name AS strategy_name, s.source_code
            FROM sim_accounts a
            JOIN strategies s ON s.id = a.strategy_id
            WHERE a.status = 'active'
              AND s.status = 'active'
            ORDER BY a.id
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def _active_account_positions(session, account_ids: list[int]) -> list[dict[str, Any]]:
    if not account_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT p.account_id, p.ts_code, p.shares, p.available_shares,
                   p.market_value, p.current_price
            FROM sim_positions p
            WHERE p.account_id = ANY(CAST(:account_ids AS INTEGER[]))
              AND p.shares > 0
            ORDER BY p.account_id, p.ts_code
            """
        ),
        {"account_ids": account_ids},
    )
    return [dict(row) for row in result.mappings().all()]


async def _pending_orders_by_account_stock(session, account_ids: list[int]) -> set[tuple[int, str]]:
    if not account_ids:
        return set()
    result = await session.execute(
        text(
            """
            SELECT DISTINCT account_id, ts_code
            FROM sim_orders
            WHERE account_id = ANY(CAST(:account_ids AS INTEGER[]))
              AND status IN ('待成交', '部分成交')
            """
        ),
        {"account_ids": account_ids},
    )
    return {(int(row["account_id"]), str(row["ts_code"])) for row in result.mappings().all()}


async def _fetch_realtime_ticks(stock_codes: list[str]) -> tuple[dict[str, RealtimeTick], str | None]:
    if not stock_codes:
        return {}, None
    try:
        ticks = await EastMoneyRealtimeProvider(sorted(set(stock_codes))).fetch_snapshot()
    except DataProviderError as exc:
        logger.debug("silent except in _fetch_realtime_ticks (exc): %s", exc)
        return {}, str(exc)
    return {tick.ts_code: tick for tick in ticks}, None


async def _recent_klines(session, ts_code: str, trade_date: date) -> list[KBar]:
    result = await session.execute(
        text(
            """
            SELECT ts_code, trade_date, open, high, low, close, pre_close,
                   volume, amount, adj_factor, is_suspended,
                   is_limit_up, is_limit_down
            FROM daily_kline
            WHERE ts_code = :ts_code AND trade_date <= :trade_date
            ORDER BY trade_date DESC
            LIMIT :limit
            """
        ),
        {"ts_code": ts_code, "trade_date": trade_date, "limit": LOOKBACK_BARS},
    )
    rows = [dict(row) for row in result.mappings().all()]
    return [_parse_kbar(row) for row in reversed(rows)]


async def _batch_klines(session, trade_date: date) -> dict[str, list[KBar]]:
    start_date = trade_date.replace(year=trade_date.year - 1)
    result = await session.execute(
        text(
            """
            SELECT ts_code, trade_date, open, high, low, close, pre_close,
                   volume, amount, adj_factor, is_suspended,
                   is_limit_up, is_limit_down
            FROM daily_kline
            WHERE trade_date BETWEEN :start AND :trade_date
            ORDER BY ts_code, trade_date DESC
            """
        ),
        {"start": start_date, "trade_date": trade_date},
    )
    rows = [dict(row) for row in result.mappings().all()]
    bars: dict[str, list[KBar]] = {}
    for row in rows:
        code = row["ts_code"]
        if code not in bars:
            bars[code] = []
        bars[code].append(_parse_kbar(row))
        if len(bars[code]) > LOOKBACK_BARS:
            bars[code].pop(0)
    for code in bars:
        bars[code].sort(key=lambda k: k.trade_date)
    return bars


async def _upsert_strategy_signal(
    session,
    *,
    user_id: int,
    request: SignalOrderRequest,
    current_position: Decimal,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    existing = await session.execute(
        text(
            """
            SELECT id
            FROM signal_log
            WHERE strategy_id = :strategy_id
              AND ts_code = :ts_code
              AND trade_date = :trade_date
              AND account_id IS NULL
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {
            "strategy_id": request.strategy_id,
            "ts_code": request.ts_code,
            "trade_date": request.trade_date,
        },
    )
    row = existing.mappings().one_or_none()
    if row is None:
        return await _insert_signal(
            session,
            user_id=user_id,
            account_id=None,
            request=request,
            current_position=current_position,
            action="PENDING",
            snapshot=snapshot,
        )

    target_position = request.target_position if request.target_position is not None else _dec(snapshot.get("target_position"))
    result = await session.execute(
        text(
            """
            UPDATE signal_log
            SET user_id = :user_id,
                signal_type = :signal_type,
                target_position = :target_position,
                current_position = :current_position,
                action = 'PENDING',
                confidence = :confidence,
                reason = :reason,
                snapshot = CAST(:snapshot AS JSONB),
                created_at = NOW()
            WHERE id = :id
            RETURNING id, user_id, strategy_id, account_id, ts_code, trade_date,
                      signal_type, target_position, current_position, action,
                      confidence, reason, snapshot, created_at
            """
        ),
        {
            "id": row["id"],
            "user_id": user_id,
            "signal_type": request.signal_type,
            "target_position": target_position,
            "current_position": current_position,
            "confidence": request.confidence,
            "reason": request.reason,
            "snapshot": __import__("json").dumps(snapshot or {}, ensure_ascii=False, default=str),
        },
    )
    return dict(result.mappings().one())


def _build_signal_request(
    *,
    strategy: dict[str, Any],
    strategy_id: int,
    ts_code: str,
    run_date: date,
    klines: list[KBar],
    current_price: Decimal | None,
    signal: dict[str, Any],
) -> tuple[SignalOrderRequest, Decimal]:
    prev_close = klines[-1].close
    snapshot: dict[str, Any] = {
        "strategy_name": strategy.get("name"),
        "prev_close": str(prev_close),
        "market_price_type": "实时行情",
        "source": "generate_all_signals",
    }
    if current_price is not None:
        snapshot["current_price"] = str(current_price)

    request = SignalOrderRequest(
        ts_code=ts_code,
        signal_type=str(signal.get("signal_type") or "观望"),
        trade_date=run_date,
        strategy_id=strategy_id,
        target_position=_dec(signal.get("target_position")) if signal.get("target_position") is not None else None,
        confidence=_dec(signal.get("confidence")) if signal.get("confidence") is not None else None,
        reason=signal.get("reason"),
        snapshot=snapshot,
    )
    current_position = Decimal(str(signal.get("current_position", 0)))
    return request, current_position


async def _evaluate_strategy_signals(
    *,
    strategy: dict[str, Any],
    strategy_id: int,
    stock_codes: list[str],
    klines_batch: dict[str, list[KBar]],
    quotes: dict[str, Decimal],
    run_date: date,
    concurrency: int,
) -> tuple[list[tuple[str, SignalOrderRequest, Decimal]], list[dict[str, Any]]]:
    effective_concurrency = min(max(int(concurrency), 1), MAX_SIGNAL_CONCURRENCY)
    semaphore = asyncio.Semaphore(effective_concurrency)

    async def evaluate_one(ts_code: str) -> tuple[str, SignalOrderRequest, Decimal] | None:
        klines = klines_batch.get(ts_code)
        if not klines:
            return None
        async with semaphore:
            current_price = quotes.get(ts_code)
            ctx = BacktestContext(klines, {}, Decimal("0"), current_price=current_price)
            signal_result = await asyncio.to_thread(_exec_strategy, strategy["source_code"], ctx)
            if not signal_result.ok:
                raise StrategySignalExecutionError(signal_result)
            signal = signal_result.signal
            if not signal:
                return None
            request, current_position = _build_signal_request(
                strategy=strategy,
                strategy_id=strategy_id,
                ts_code=ts_code,
                run_date=run_date,
                klines=klines,
                current_price=current_price,
                signal=signal,
            )
            return ts_code, request, current_position

    tasks = [evaluate_one(ts_code) for ts_code in stock_codes if ts_code in klines_batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    evaluated: list[tuple[str, SignalOrderRequest, Decimal]] = []
    errors: list[dict[str, Any]] = []
    task_codes = [ts_code for ts_code in stock_codes if ts_code in klines_batch]
    for ts_code, result in zip(task_codes, results, strict=False):
        if isinstance(result, StrategySignalExecutionError):
            errors.append({
                "strategy_id": strategy_id,
                "ts_code": ts_code,
                "error": str(result),
                **result.result.to_error_dict(),
            })
        elif isinstance(result, Exception):
            errors.append({"strategy_id": strategy_id, "ts_code": ts_code, "error": str(result)})
        elif result is not None:
            evaluated.append(result)
    return evaluated, errors


def _enrich_and_sort_signals(
    evaluated_signals: list[tuple[str, SignalOrderRequest, Decimal]],
) -> list[tuple[str, SignalOrderRequest, Decimal]]:
    enriched: list[tuple[str, SignalOrderRequest, Decimal, Decimal]] = []
    for ts_code, request, current_position in evaluated_signals:
        priority_score = Decimal("0")
        priority_source = "default"
        if request.signal_type in BUY_SIGNAL_TYPES and request.confidence is not None:
            priority_score = request.confidence
            priority_source = "confidence"

        if request.signal_type in BUY_SIGNAL_TYPES:
            snapshot = dict(request.snapshot or {})
            snapshot.update(
                {
                    "buy_priority_score": str(priority_score),
                    "buy_priority_source": priority_source,
                }
            )
            request = replace(request, snapshot=snapshot)

        enriched.append((ts_code, request, current_position, priority_score))

    def sort_key(item: tuple[str, SignalOrderRequest, Decimal, Decimal]) -> tuple[int, int, Decimal, Decimal, str]:
        ts_code, request, _current_position, priority_score = item
        if request.signal_type in EXIT_SIGNAL_TYPES:
            group = 0
        elif request.signal_type in BUY_SIGNAL_TYPES:
            group = 1
        else:
            group = 2
        target_position = request.target_position if request.target_position is not None else Decimal("0")
        return group, 0, -priority_score, -target_position, ts_code

    return [(ts_code, request, current_position) for ts_code, request, current_position, *_ in sorted(enriched, key=sort_key)]


async def generate_all_signals_for_date(session, *, trade_date: date | None = None, concurrency: int | None = None) -> dict[str, Any]:
    run_date = await _resolve_signal_trade_date(session, trade_date)
    if run_date is None:
        return {"skipped": True, "reason": "no synced kline or open trade date available"}

    strategies = await _active_strategies(session)
    stock_codes = await _stock_codes(session)
    quotes = await _fetch_midday_quotes(stock_codes)
    effective_concurrency = concurrency if concurrency is not None else await get_full_kline_sync_concurrency(session)
    effective_concurrency = min(max(int(effective_concurrency), 1), MAX_SIGNAL_CONCURRENCY)
    stats: dict[str, Any] = {
        "trade_date": run_date.isoformat(),
        "strategy_count": len(strategies),
        "stock_count": len(stock_codes),
        "quotes_fetched": len(quotes),
        "concurrency": effective_concurrency,
        "signals_logged": 0,
        "orders_created": 0,
        "orders_skipped": 0,
        "order_skip_reasons": [],
        "errors": [],
    }
    accounts_by_strategy: dict[int, list[dict[str, Any]]] = {}
    COMMIT_INTERVAL = 500

    for strategy in strategies:
        strategy_id = int(strategy["id"])
        accounts_by_strategy[strategy_id] = await _bound_accounts(session, strategy_id)
        klines_batch = await _batch_klines(session, run_date)
        evaluated_signals, eval_errors = await _evaluate_strategy_signals(
            strategy=strategy,
            strategy_id=strategy_id,
            stock_codes=stock_codes,
            klines_batch=klines_batch,
            quotes=quotes,
            run_date=run_date,
            concurrency=effective_concurrency,
        )
        stats["errors"].extend(eval_errors)
        evaluated_signals = _enrich_and_sort_signals(evaluated_signals)

        for ts_code, request, current_position in evaluated_signals:
            try:
                async with session.begin_nested():
                    strategy_signal = await _upsert_strategy_signal(
                        session,
                        user_id=int(strategy["user_id"]),
                        request=request,
                        current_position=current_position,
                        snapshot=request.snapshot or {},
                    )
                    stats["signals_logged"] += 1

                    for account in accounts_by_strategy[strategy_id]:
                        order_result = await generate_order_from_signal(
                            session,
                            user_id=int(account["user_id"]),
                            account_id=int(account["id"]),
                            request=request,
                            strategy_signal_id=int(strategy_signal["id"]),
                            auto_commit=False,
                            auto_match=True,
                            auto_match_mode="close",
                        )
                        if order_result.get("order") is not None:
                            stats["orders_created"] += 1
                        elif request.signal_type != "观望":
                            stats["orders_skipped"] += 1
                            if len(stats["order_skip_reasons"]) < 50:
                                stats["order_skip_reasons"].append(
                                    {
                                        "strategy_id": strategy_id,
                                        "account_id": int(account["id"]),
                                        "ts_code": ts_code,
                                        "signal_id": int(strategy_signal["id"]),
                                        "action": order_result.get("action"),
                                        "reason": order_result.get("reason") or "未生成委托",
                                    }
                                )

                if stats["signals_logged"] % COMMIT_INTERVAL == 0:
                    await session.commit()
            except Exception as exc:
                logger.warning("silent except in generate_all_signals_for_date (exc)", exc_info=True)
                stats["errors"].append({"strategy_id": strategy_id, "ts_code": ts_code, "error": str(exc)})

    await session.commit()
    stats["error_count"] = len(stats["errors"])
    return stats


def _publish_signal_batch(stats: dict[str, Any]) -> None:
    """Publish a signal batch summary to Redis for WebSocket fanout."""
    import redis as redis_sync

    from app.core.config import get_settings

    try:
        client = redis_sync.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)
        payload = {
            "type": "signal_batch",
            "trade_date": stats.get("trade_date"),
            "strategy_count": stats.get("strategy_count", 0),
            "signals_logged": stats.get("signals_logged", 0),
            "orders_created": stats.get("orders_created", 0),
            "error_count": stats.get("error_count", 0),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        client.publish(SIGNAL_EVENTS_CHANNEL, json.dumps(payload, ensure_ascii=False, default=str))
        client.close()
    except Exception:
        import logging

        logging.getLogger(__name__).debug("Failed to publish signal batch to Redis", exc_info=True)


def _intraday_order_price(signal_type: str, tick: RealtimeTick) -> Decimal:
    if signal_type in BUY_SIGNAL_TYPES:
        return tick.ask1 or tick.price
    return tick.bid1 or tick.price


def _intraday_signal_request(
    *,
    account: dict[str, Any],
    position: dict[str, Any],
    tick: RealtimeTick,
    run_date: date,
    signal: dict[str, Any],
    current_position: Decimal,
) -> SignalOrderRequest:
    signal_type = str(signal.get("signal_type") or "观望")
    snapshot = {
        "source": "generate_intraday_position_signals",
        "strategy_name": account.get("strategy_name"),
        "current_price": str(tick.price),
        "bid1": str(tick.bid1) if tick.bid1 is not None else None,
        "ask1": str(tick.ask1) if tick.ask1 is not None else None,
        "current_position": str(current_position),
        "shares": int(position.get("shares") or 0),
        "available_shares": int(position.get("available_shares") or 0),
        "market_price_type": "实时盘口",
    }
    return SignalOrderRequest(
        ts_code=str(position["ts_code"]),
        signal_type=signal_type,
        trade_date=run_date,
        strategy_id=int(account["strategy_id"]),
        target_position=_dec(signal.get("target_position")) if signal.get("target_position") is not None else None,
        confidence=_dec(signal.get("confidence")) if signal.get("confidence") is not None else None,
        reason=signal.get("reason"),
        snapshot=snapshot,
    )


async def generate_intraday_position_signals_for_date(
    session,
    *,
    trade_date: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    run_date = trade_date or date.today()
    if not await _is_open_trade_day(session, run_date):
        return {"trade_date": run_date.isoformat(), "skipped": True, "reason": "non-trading day"}
    if not _is_intraday_trading_time(now):
        return {"trade_date": run_date.isoformat(), "skipped": True, "reason": "outside intraday trading hours"}

    accounts = await _active_accounts_with_strategies(session)
    account_ids = [int(account["id"]) for account in accounts]
    positions = await _active_account_positions(session, account_ids)
    ticks, quote_error = await _fetch_realtime_ticks([str(position["ts_code"]) for position in positions])
    pending_orders = await _pending_orders_by_account_stock(session, account_ids)

    accounts_by_id = {int(account["id"]): account for account in accounts}
    stats: dict[str, Any] = {
        "trade_date": run_date.isoformat(),
        "account_count": len(accounts),
        "position_count": len(positions),
        "quotes_fetched": len(ticks),
        "signals_logged": 0,
        "orders_created": 0,
        "orders_skipped": 0,
        "order_skip_reasons": [],
        "errors": [],
    }
    if quote_error:
        stats["errors"].append({"stage": "fetch_realtime_ticks", "error": quote_error})

    for position in positions:
        account_id = int(position["account_id"])
        account = accounts_by_id.get(account_id)
        ts_code = str(position["ts_code"])
        if account is None:
            continue
        tick = ticks.get(ts_code)
        if tick is None:
            stats["orders_skipped"] += 1
            if len(stats["order_skip_reasons"]) < 50:
                stats["order_skip_reasons"].append({"account_id": account_id, "ts_code": ts_code, "reason": "无实时行情"})
            continue
        if (account_id, ts_code) in pending_orders:
            stats["orders_skipped"] += 1
            if len(stats["order_skip_reasons"]) < 50:
                stats["order_skip_reasons"].append({"account_id": account_id, "ts_code": ts_code, "reason": "已有待成交委托"})
            continue

        try:
            klines = await _recent_klines(session, ts_code, run_date)
            if not klines:
                stats["orders_skipped"] += 1
                if len(stats["order_skip_reasons"]) < 50:
                    stats["order_skip_reasons"].append({"account_id": account_id, "ts_code": ts_code, "reason": "无历史K线"})
                continue

            total_asset = _dec(account.get("total_asset"))
            market_value = _money(tick.price * Decimal(int(position.get("shares") or 0)))
            current_position = (market_value / total_asset) if total_asset > 0 else Decimal("0")
            ctx = BacktestContext(klines, {}, total_asset, current_price=tick.price)
            ctx.current_position = float(current_position)
            signal_result = await asyncio.to_thread(_exec_strategy, str(account["source_code"]), ctx)
            if not signal_result.ok:
                stats["errors"].append({
                    "account_id": account_id,
                    "strategy_id": int(account["strategy_id"]),
                    "ts_code": ts_code,
                    "error": signal_result.error_summary(),
                    **signal_result.to_error_dict(),
                })
                continue
            signal = signal_result.signal
            if not signal:
                continue
            signal_type = str(signal.get("signal_type") or "观望")
            if signal_type == "买入":
                stats["orders_skipped"] += 1
                if len(stats["order_skip_reasons"]) < 50:
                    stats["order_skip_reasons"].append({"account_id": account_id, "ts_code": ts_code, "reason": "盘中持仓调仓忽略买入信号"})
                continue
            if signal_type not in INTRADAY_POSITION_SIGNAL_TYPES:
                stats["orders_skipped"] += 1
                if len(stats["order_skip_reasons"]) < 50:
                    stats["order_skip_reasons"].append({"account_id": account_id, "ts_code": ts_code, "reason": f"不支持的信号: {signal_type}"})
                continue

            request = _intraday_signal_request(
                account=account,
                position=position,
                tick=tick,
                run_date=run_date,
                signal=signal,
                current_position=current_position,
            )
            order_price = _intraday_order_price(signal_type, tick)
            async with session.begin_nested():
                order_result = await generate_order_from_signal(
                    session,
                    user_id=int(account["user_id"]),
                    account_id=account_id,
                    request=request,
                    order_price_override=order_price,
                    auto_commit=False,
                    allow_missing_kline_with_order_price=True,
                    auto_match=True,
                    auto_match_mode="limit",
                )
                stats["signals_logged"] += 1
                if order_result.get("order") is not None:
                    stats["orders_created"] += 1
                    pending_orders.add((account_id, ts_code))
                elif signal_type != "观望":
                    stats["orders_skipped"] += 1
                    if len(stats["order_skip_reasons"]) < 50:
                        stats["order_skip_reasons"].append(
                            {
                                "account_id": account_id,
                                "ts_code": ts_code,
                                "action": order_result.get("action"),
                                "reason": order_result.get("reason") or "未生成委托",
                            }
                        )
        except Exception as exc:
            logger.warning("silent except in generate_intraday_position_signals_for_date (exc)", exc_info=True)
            stats["errors"].append({"account_id": account_id, "ts_code": ts_code, "error": str(exc)})

    await session.commit()
    stats["error_count"] = len(stats["errors"])
    return stats


@celery_app.task(
    name="app.tasks.signal_tasks.generate_all_signals",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
@with_beat_lock("app.tasks.signal_tasks.generate_all_signals")
def generate_all_signals(self, trade_date: str | None = None, concurrency: int | None = None) -> dict[str, Any]:
    run_date = date.fromisoformat(trade_date) if trade_date else None
    result = run_async(
        _run_tracked(
            "generate_all_signals",
            self.request.id,
            {"trade_date": run_date, "concurrency": concurrency},
            with_session(generate_all_signals_for_date, trade_date=run_date, concurrency=concurrency),
        )
    )
    _publish_signal_batch(result)
    return result


@celery_app.task(
    name="app.tasks.signal_tasks.generate_intraday_position_signals",
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    autoretry_for=(DataProviderError, ConnectionError, TimeoutError),
)
def generate_intraday_position_signals(self, trade_date: str | None = None) -> dict[str, Any]:
    run_date = date.fromisoformat(trade_date) if trade_date else None
    return run_async(
        _run_tracked(
            "generate_intraday_position_signals",
            self.request.id,
            {"trade_date": run_date},
            with_session(generate_intraday_position_signals_for_date, trade_date=run_date),
        )
    )
