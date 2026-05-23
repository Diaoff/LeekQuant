from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
import pytest_asyncio
import redis
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.data.repository import create_pending_task_run
from app.db.session import get_session
from app.factor.service import analyze_factor_icir, compute_factors_for_date, query_factor_analysis, query_rank
from app.main import app
from app.tasks import tracking
from app.tasks.factor_tasks import compute_daily_factors

TEST_CODES = ("900001.SZ", "900002.SZ", "900003.SZ")
TEST_GROUP = "M5集成验证"
TEST_DATES = (date(2026, 3, 1), date(2026, 3, 2))
TEST_NON_TRADING_DATE = date(2026, 3, 3)
TEST_TASK_PREFIX = "m5-integration-"


@pytest.fixture(scope="module")
def integration_database_url() -> str:
    try:
        database_url = os.environ.get("M5_INTEGRATION_DATABASE_URL") or get_settings().database_url
    except Exception as exc:  # pragma: no cover - failure message is the useful behavior.
        pytest.fail(f"M5 integration tests require DATABASE_URL or M5_INTEGRATION_DATABASE_URL: {exc}")
    if not database_url:
        pytest.fail("M5 integration tests require non-empty DATABASE_URL or M5_INTEGRATION_DATABASE_URL")
    print(f"M5 integration database target: {make_url(database_url).render_as_string(hide_password=True)}")
    return database_url


@pytest.fixture(scope="module", autouse=True)
def require_redis() -> None:
    try:
        client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
    except Exception as exc:  # pragma: no cover - depends on local integration environment.
        pytest.fail(f"M5 integration tests require reachable Redis at REDIS_URL: {exc}")


@pytest.fixture(scope="module", autouse=True)
def alembic_upgrade_head(integration_database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = integration_database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


@pytest_asyncio.fixture()
async def integration_session_factory(integration_database_url: str):
    engine = create_async_engine(integration_database_url, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _cleanup(session) -> None:
    params = {"group_name": TEST_GROUP, "codes": list(TEST_CODES)}
    for statement in [
        "DELETE FROM factor_analysis WHERE factor_name IN ('roe', 'pe_ttm') AND period_start >= DATE '2026-03-01' AND period_end <= DATE '2026-03-05'",
        "DELETE FROM scoring_rank WHERE ts_code = ANY(CAST(:codes AS VARCHAR[])) OR scope_value = :group_name",
        "DELETE FROM factor_values WHERE ts_code = ANY(CAST(:codes AS VARCHAR[]))",
        "DELETE FROM daily_kline WHERE ts_code = ANY(CAST(:codes AS VARCHAR[]))",
        "DELETE FROM stock_fundamentals WHERE ts_code = ANY(CAST(:codes AS VARCHAR[]))",
        "DELETE FROM watchlist WHERE ts_code = ANY(CAST(:codes AS VARCHAR[])) OR group_name = :group_name",
        "DELETE FROM watchlist_groups WHERE group_name = :group_name",
        "DELETE FROM stock_basic WHERE ts_code = ANY(CAST(:codes AS VARCHAR[]))",
        "DELETE FROM task_runs WHERE task_id LIKE :task_prefix OR payload->>'scope_value' = :group_name",
    ]:
        await session.execute(text(statement), {**params, "task_prefix": f"{TEST_TASK_PREFIX}%"})
    await session.commit()


async def _seed_market_fixture(session) -> None:
    await _cleanup(session)
    await session.execute(
        text(
            """
            INSERT INTO stock_basic (ts_code, symbol, name, market, exchange, industry, is_delisted, data_source)
            VALUES
                ('900001.SZ', '900001', 'M5样本一', '主板', 'SZSE', '银行', FALSE, 'test'),
                ('900002.SZ', '900002', 'M5样本二', '主板', 'SZSE', '消费', FALSE, 'test'),
                ('900003.SZ', '900003', 'M5样本三', '主板', 'SZSE', '制造', FALSE, 'test')
            ON CONFLICT (ts_code) DO UPDATE SET
                name = EXCLUDED.name,
                is_delisted = FALSE,
                updated_at = NOW()
            """
        )
    )
    await session.execute(
        text(
            """
            INSERT INTO watchlist_groups (user_id, group_name)
            VALUES (1, :group_name)
            ON CONFLICT (user_id, group_name) DO NOTHING
            """
        ),
        {"group_name": TEST_GROUP},
    )
    await session.execute(
        text(
            """
            INSERT INTO watchlist (user_id, group_name, ts_code, sort_order)
            VALUES
                (1, :group_name, '900001.SZ', 1),
                (1, :group_name, '900002.SZ', 2)
            ON CONFLICT (user_id, group_name, ts_code) DO UPDATE SET
                sort_order = EXCLUDED.sort_order,
                updated_at = NOW()
            """
        ),
        {"group_name": TEST_GROUP},
    )
    await session.execute(
        text(
            """
            INSERT INTO stock_fundamentals (ts_code, report_date, pe_ttm, pb, roe, revenue_growth, data_source)
            VALUES
                ('900001.SZ', DATE '2026-02-28', 8.0, 0.8, 0.18, 0.15, 'test'),
                ('900002.SZ', DATE '2026-02-28', 12.0, 1.2, 0.11, 0.09, 'test'),
                ('900003.SZ', DATE '2026-02-28', 20.0, 2.0, 0.05, 0.03, 'test')
            ON CONFLICT (ts_code, report_date) DO UPDATE SET
                pe_ttm = EXCLUDED.pe_ttm,
                pb = EXCLUDED.pb,
                roe = EXCLUDED.roe,
                revenue_growth = EXCLUDED.revenue_growth,
                data_source = EXCLUDED.data_source,
                updated_at = NOW()
            """
        )
    )
    calendar_rows = [
        {"cal_date": date(2026, 1, 1) + timedelta(days=offset), "is_open": date(2026, 1, 1) + timedelta(days=offset) != TEST_NON_TRADING_DATE}
        for offset in range(76)
    ]
    await session.execute(
        text(
            """
            INSERT INTO trade_calendar (cal_date, is_open, source)
            VALUES (:cal_date, :is_open, 'test')
            ON CONFLICT (cal_date) DO UPDATE SET
                is_open = EXCLUDED.is_open,
                source = EXCLUDED.source,
                updated_at = NOW()
            """
        ),
        calendar_rows,
    )
    kline_rows = []
    for code, base, step in [
        ("900001.SZ", Decimal("10"), Decimal("0.050")),
        ("900002.SZ", Decimal("12"), Decimal("0.025")),
        ("900003.SZ", Decimal("8"), Decimal("0.010")),
    ]:
        for offset in range(76):
            close = base + step * offset
            trade_day = date(2026, 1, 1) + timedelta(days=offset)
            kline_rows.append(
                {
                    "ts_code": code,
                    "trade_date": trade_day,
                    "open": close - Decimal("0.01"),
                    "high": close + Decimal("0.03"),
                    "low": close - Decimal("0.03"),
                    "close": close,
                    "pre_close": close - step if offset else close,
                    "volume": 100000 + offset,
                    "amount": close * Decimal(100000 + offset),
                }
            )
    await session.execute(
        text(
            """
            INSERT INTO daily_kline (
                ts_code, trade_date, open, high, low, close, pre_close,
                volume, amount, is_suspended, data_source, raw_payload
            )
            VALUES (
                :ts_code, :trade_date, :open, :high, :low, :close, :pre_close,
                :volume, :amount, FALSE, 'test', '{}'::JSONB
            )
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                pre_close = EXCLUDED.pre_close,
                volume = EXCLUDED.volume,
                amount = EXCLUDED.amount,
                is_suspended = FALSE,
                data_source = EXCLUDED.data_source,
                updated_at = NOW()
            """
        ),
        kline_rows,
    )
    await session.commit()


@pytest_asyncio.fixture()
async def seeded_m5_data(integration_session_factory):
    async with integration_session_factory() as session:
        await _seed_market_fixture(session)
    yield
    async with integration_session_factory() as session:
        await _cleanup(session)


@pytest.mark.asyncio
async def test_m5_real_db_factor_compute_analysis_and_query_api(integration_session_factory, seeded_m5_data):
    async with integration_session_factory() as session:
        for trade_day in TEST_DATES:
            result = await compute_factors_for_date(session, trade_date=trade_day)
            assert result["factor_value_count"] >= 12
            assert result["rank_count"] >= 3

        group_result = await compute_factors_for_date(
            session,
            trade_date=TEST_DATES[-1],
            scope_type="watchlist_group",
            scope_value=TEST_GROUP,
        )
        assert group_result["rank_count"] == 2

        analysis = await analyze_factor_icir(
            session,
            factor_name="roe",
            period_start=TEST_DATES[0],
            period_end=TEST_DATES[-1],
            forward_days=1,
        )
        assert analysis["ic_count"] == 2

        rank = await query_rank(session, trade_date=TEST_DATES[-1], page_size=10)
        assert rank["total"] >= 3

        scoped_rank = await query_rank(
            session,
            trade_date=TEST_DATES[-1],
            scope_type="watchlist_group",
            scope_value=TEST_GROUP,
            page_size=10,
        )
        assert scoped_rank["total"] == 2
        assert {item["ts_code"] for item in scoped_rank["items"]} == {"900001.SZ", "900002.SZ"}

        await session.execute(
            text("DELETE FROM watchlist WHERE user_id = 1 AND group_name = :group_name AND ts_code = '900002.SZ'"),
            {"group_name": TEST_GROUP},
        )
        await session.commit()
        reduced_group = await compute_factors_for_date(
            session,
            trade_date=TEST_DATES[-1],
            scope_type="watchlist_group",
            scope_value=TEST_GROUP,
        )
        assert reduced_group["rank_count"] == 1
        reduced_rank = await query_rank(
            session,
            trade_date=TEST_DATES[-1],
            scope_type="watchlist_group",
            scope_value=TEST_GROUP,
            page_size=10,
        )
        assert reduced_rank["total"] == 1
        assert {item["ts_code"] for item in reduced_rank["items"]} == {"900001.SZ"}

        skipped = await compute_factors_for_date(session, trade_date=TEST_NON_TRADING_DATE)
        assert skipped == {"skipped": True, "reason": "non-trading day"}
        non_trading_values = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM factor_values
                    WHERE trade_date = :trade_date
                      AND ts_code = ANY(CAST(:codes AS VARCHAR[]))
                    """
                ),
                {"trade_date": TEST_NON_TRADING_DATE, "codes": list(TEST_CODES)},
            )
        ).scalar_one()
        assert non_trading_values == 0

        analysis_page = await query_factor_analysis(session, factor_name="roe")
        assert analysis_page["total"] >= 1

    async def override_session():
        async with integration_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get(f"/api/factors/rank?trade_date={TEST_DATES[-1].isoformat()}&page_size=3")
        assert response.status_code == 200
        assert response.json()["total"] >= 3

        scoped = client.get(
            f"/api/factors/rank?trade_date={TEST_DATES[-1].isoformat()}&scope_type=watchlist_group&scope_value={TEST_GROUP}&page_size=3"
        )
        assert scoped.status_code == 200
        assert scoped.json()["total"] == 1
        assert {item["ts_code"] for item in scoped.json()["items"]} == {"900001.SZ"}

        factors = client.get("/api/factors?enabled_only=true")
        assert factors.status_code == 200
        assert any(item["name"] == "roe" for item in factors.json())
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_m5_factor_task_updates_real_task_run(integration_session_factory, seeded_m5_data, monkeypatch):
    task_id = f"{TEST_TASK_PREFIX}{uuid4().hex}"
    payload = {
        "trade_date": TEST_DATES[-1].isoformat(),
        "scope_type": "watchlist_group",
        "scope_value": TEST_GROUP,
    }
    async with integration_session_factory() as session:
        await create_pending_task_run(
            session,
            task_name="compute_daily_factors",
            task_id=task_id,
            payload=payload,
        )

    monkeypatch.setattr(tracking, "async_session_factory", integration_session_factory)

    def run_task() -> dict:
        compute_daily_factors.push_request(id=task_id)
        try:
            return compute_daily_factors.run(**payload)
        finally:
            compute_daily_factors.pop_request()

    result = await asyncio.to_thread(run_task)

    assert result["rank_count"] == 2
    async with integration_session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT status, result, error_message
                    FROM task_runs
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).mappings().one()

    assert row["status"] == "success"
    assert row["error_message"] is None
    assert row["result"]["rank_count"] == 2


@pytest.mark.asyncio
async def test_m5_factor_task_records_failed_status_after_business_sql_error(integration_session_factory, seeded_m5_data, monkeypatch):
    task_id = f"{TEST_TASK_PREFIX}{uuid4().hex}"
    payload = {
        "trade_date": TEST_DATES[-1].isoformat(),
        "scope_type": "watchlist_group",
        "scope_value": TEST_GROUP,
    }
    async with integration_session_factory() as session:
        await create_pending_task_run(
            session,
            task_name="compute_daily_factors",
            task_id=task_id,
            payload=payload,
        )

    async def failing_compute(session, **_kwargs):
        await session.execute(text("SELECT * FROM m5_missing_failure_probe"))
        return {"ok": True}

    monkeypatch.setattr(tracking, "async_session_factory", integration_session_factory)
    monkeypatch.setattr("app.tasks.factor_tasks.compute_factors_for_date", failing_compute)

    def run_task() -> None:
        compute_daily_factors.push_request(id=task_id)
        try:
            compute_daily_factors.run(**payload)
        finally:
            compute_daily_factors.pop_request()

    with pytest.raises(Exception, match="m5_missing_failure_probe"):
        await asyncio.to_thread(run_task)

    async with integration_session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT status, result, error_message, finished_at
                    FROM task_runs
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).mappings().one()

    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert "m5_missing_failure_probe" in row["error_message"]
