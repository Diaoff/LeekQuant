"""Tests for the circuit breaker — focuses on the P1 NEW-1 fix where
``filter_open_circuits`` is called from async land BEFORE
``asyncio.to_thread(fetch_with_fallback, ...)`` so that ``failure_count``
actually short-circuits a failing provider.

Previous bug: ``_breaker_sync_check`` ran inside ``fetch_with_fallback`` which
itself runs inside a worker thread (via ``asyncio.to_thread``). Inside the
worker thread, ``asyncio.get_event_loop().is_running()`` returns True (the
main loop is still running), so the sync wrapper returned False (fail-open)
and the breaker was never triggered — even when ``failure_count`` was well
above threshold. Worse: no async caller passed ``data_type``/``session`` to
``fetch_with_fallback`` anyway, so the breaker was effectively dead code.

This file verifies the new architecture:

1. ``filter_open_circuits`` is async, uses ``AsyncSession`` correctly,
   and filters out OPEN providers.
2. ``fetch_with_fallback`` no longer has ``data_type``/``session`` params
   (clean cut — caller filters beforehand).
3. ``_breaker_sync_check`` is deleted.
4. When all providers' circuits are OPEN, async callers raise
   ``DataProviderError("all providers circuit-open ...")`` without
   burning ``max_retries`` on each provider.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.data.circuit_breaker import CircuitBreaker
from app.data.fetcher import (
    DataProviderError,
    _get_breaker,
    fetch_with_fallback,
    filter_open_circuits,
    reset_breaker_for_tests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Minimal provider stub — just needs a ``name`` attribute."""

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"_FakeProvider({self.name!r})"


class _FakeResult:
    """Result row for breaker.is_open's SELECT."""

    def __init__(self, failure_count: int | None, last_failure_at: datetime | None):
        self.failure_count = failure_count
        self.last_failure_at = last_failure_at

    # SQLAlchemy Row-like access — circuit_breaker.py uses ``row.failure_count``
    # and ``row.last_failure_at`` directly (attribute access, not subscript).
    # When the row is None, breaker.is_open returns False early, so we never
    # need to worry about None having attributes.


class _FakeSession:
    """AsyncSession stub that returns scripted rows for specific SQL patterns."""

    def __init__(self, rows_by_data_type: dict[str, _FakeResult | None]):
        self._rows = rows_by_data_type
        self.executed: list[str] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append(sql)
        # Match the breaker's query: SELECT failure_count, last_failure_at
        # FROM data_update_state WHERE data_type = :dt AND source = :s
        # params look like {"dt": "daily_kline", "s": "adata"}
        data_type = (params or {}).get("dt")
        if data_type in self._rows:
            row = self._rows[data_type]
        else:
            row = None
        # Mimic Result.first() returning None or a Row-like object.
        class _Result:
            def __init__(self, r):
                self._r = r

            def first(self):
                return self._r

        return _Result(row)


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Each test gets a fresh breaker singleton."""
    reset_breaker_for_tests()
    yield
    reset_breaker_for_tests()


# ---------------------------------------------------------------------------
# filter_open_circuits — the new async-side breaker entry point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_open_circuits_passthrough_when_threshold_disabled(monkeypatch):
    """When threshold <= 0, filter_open_circuits is a no-op pass-through.

    No DB query should be issued — breaker.is_open returns False immediately
    when threshold <= 0.
    """
    breaker = _get_breaker()
    monkeypatch.setattr(breaker, "threshold", 0)

    session = _FakeSession({})
    providers = [_FakeProvider("adata"), _FakeProvider("baostock")]

    result = await filter_open_circuits(session, providers, "daily_kline")

    assert result == providers  # all kept, no filtering
    assert session.executed == []  # no DB query


@pytest.mark.asyncio
async def test_filter_open_circuits_skips_open_provider():
    """When failure_count >= threshold and within cooldown window, the
    provider's circuit is OPEN and filter_open_circuits skips it.

    Note: filter_open_circuits uses is_open_batch — one SELECT with
    source = ANY(:sources) + GROUP BY source. The stub session matches
    on that pattern.
    """
    breaker = _get_breaker()
    breaker.threshold = 3
    breaker.cooldown = timedelta(seconds=60)

    now = datetime.now(timezone.utc)

    class _FakeRow:
        def __init__(self, source, failure_count, last_failure_at):
            self.source = source
            self.failure_count = failure_count
            self.last_failure_at = last_failure_at

    class _BatchSession:
        """Session stub that matches the is_open_batch SQL pattern."""

        def __init__(self):
            self.executed = []
            # Map source -> (failure_count, last_failure_at) or None.
            # Mimics the data_update_state table state.
            self._rows_by_source = {
                "adata": _FakeRow(
                    source="adata",
                    failure_count=5,
                    last_failure_at=now - timedelta(seconds=10),  # OPEN
                ),
                "baostock": _FakeRow(
                    source="baostock",
                    failure_count=1,  # below threshold → CLOSED
                    last_failure_at=now - timedelta(seconds=10),
                ),
                # akshare: not in table → CLOSED
            }

        async def execute(self, statement, params=None):
            self.executed.append(str(statement))
            sql = str(statement)
            if "failure_count" in sql and "data_update_state" in sql:
                # Match the batched query: WHERE source = ANY(:sources)
                # Return only rows that exist in our stub.
                sources_param = (params or {}).get("sources", [])
                rows = [
                    self._rows_by_source[s]
                    for s in sources_param
                    if s in self._rows_by_source
                ]
                class _Result:
                    def all(self):
                        return rows
                return _Result()
            # Unknown query — return empty
            class _EmptyResult:
                def first(self):
                    return None

                def all(self):
                    return []
            return _EmptyResult()

    session = _BatchSession()
    providers = [_FakeProvider("adata"), _FakeProvider("baostock"), _FakeProvider("akshare")]

    result = await filter_open_circuits(session, providers, "daily_kline")

    # adata (failure_count=5 >= 3, within cooldown) → OPEN, skipped
    # baostock (failure_count=1 < 3) → CLOSED, kept
    # akshare (no row) → CLOSED, kept
    assert [p.name for p in result] == ["baostock", "akshare"]
    # ONE query issued — that's the point of the batched refactor.
    assert len(session.executed) == 1


@pytest.mark.asyncio
async def test_filter_open_circuits_half_open_after_cooldown():
    """When cooldown has elapsed, the breaker goes HALF-OPEN and allows
    one attempt — provider is kept (not filtered)."""
    breaker = _get_breaker()
    breaker.threshold = 3
    breaker.cooldown = timedelta(seconds=60)

    # failure_count=5 (above threshold) but last_failure_at=120s ago → cooldown elapsed
    now = datetime.now(timezone.utc)

    class _FakeRow:
        def __init__(self, source, failure_count, last_failure_at):
            self.source = source
            self.failure_count = failure_count
            self.last_failure_at = last_failure_at

    class _BatchSession:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "failure_count" in sql and "data_update_state" in sql:
                rows = [
                    _FakeRow(
                        source="adata",
                        failure_count=5,
                        last_failure_at=now - timedelta(seconds=120),  # > 60s cooldown
                    )
                ]
                class _Result:
                    def all(self):
                        return rows
                return _Result()
            class _EmptyResult:
                def all(self):
                    return []
            return _EmptyResult()

    providers = [_FakeProvider("adata")]

    result = await filter_open_circuits(_BatchSession(), providers, "daily_kline")

    assert len(result) == 1  # kept (half-open allows attempt)


@pytest.mark.asyncio
async def test_filter_open_circuits_fail_open_on_db_error():
    """When the breaker's DB query raises (e.g. DB unavailable), the provider
    is KEPT (fail-open) rather than dropped — the fetch itself will surface
    the real error. This matches the previous fail-open semantics but ONLY
    for unexpected breaker errors, not for normal operation."""
    breaker = _get_breaker()
    breaker.threshold = 3

    class _BrokenSession:
        async def execute(self, statement, params=None):
            raise RuntimeError("DB unavailable")

    providers = [_FakeProvider("adata")]
    result = await filter_open_circuits(_BrokenSession(), providers, "daily_kline")

    assert result == providers  # all kept (fail-open)


@pytest.mark.asyncio
async def test_filter_open_circuits_returns_empty_when_all_open():
    """When all providers' circuits are OPEN, filter_open_circuits returns [].
    The async caller is then responsible for raising DataProviderError
    ('all providers circuit-open ...')."""
    breaker = _get_breaker()
    breaker.threshold = 3
    breaker.cooldown = timedelta(seconds=60)

    now = datetime.now(timezone.utc)

    class _FakeRow:
        def __init__(self, source, failure_count, last_failure_at):
            self.source = source
            self.failure_count = failure_count
            self.last_failure_at = last_failure_at

    class _BatchSession:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "failure_count" in sql and "data_update_state" in sql:
                sources_param = (params or {}).get("sources", [])
                # Return every requested source as OPEN
                rows = [
                    _FakeRow(
                        source=s,
                        failure_count=5,
                        last_failure_at=now - timedelta(seconds=10),  # within cooldown
                    )
                    for s in sources_param
                ]
                class _Result:
                    def all(self):
                        return rows
                return _Result()
            class _EmptyResult:
                def all(self):
                    return []
            return _EmptyResult()

    providers = [_FakeProvider("adata"), _FakeProvider("baostock")]
    result = await filter_open_circuits(_BatchSession(), providers, "daily_kline")

    assert result == []  # all filtered out


# ---------------------------------------------------------------------------
# fetch_with_fallback — verify it no longer accepts data_type/session
# ---------------------------------------------------------------------------


def test_fetch_with_fallback_signature_drops_data_type_and_session():
    """fetch_with_fallback no longer accepts data_type/session params —
    the breaker is consulted from the async caller via filter_open_circuits
    before to_thread. The signature was a footgun: passing an AsyncSession
    to a sync function running in a worker thread cannot work."""
    import inspect

    sig = inspect.signature(fetch_with_fallback)
    params = set(sig.parameters.keys())
    assert "data_type" not in params, (
        "fetch_with_fallback must NOT accept data_type — use filter_open_circuits "
        "from the async caller instead."
    )
    assert "session" not in params, (
        "fetch_with_fallback must NOT accept session — AsyncSession cannot be "
        "used from a worker thread."
    )
    # Sanity: still accepts proxy_url
    assert "proxy_url" in params


def test_breaker_sync_check_deleted():
    """_breaker_sync_check must be deleted — it was the source of the
    fail-open bug (returned False inside a running event loop)."""
    from app.data import fetcher

    assert not hasattr(fetcher, "_breaker_sync_check"), (
        "_breaker_sync_check must be deleted — its loop.is_running() check "
        "always returned True inside asyncio.to_thread, making the breaker "
        "perpetually fail-open."
    )


# ---------------------------------------------------------------------------
# Integration: sync_stock_basic / sync_kline raise when all circuits open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_stock_basic_raises_when_all_circuits_open(monkeypatch):
    """When all providers' circuits are OPEN, sync_stock_basic should raise
    DataProviderError('all providers circuit-open ...') WITHOUT calling
    fetch_with_fallback at all — saving max_retries * len(providers) wasted
    attempts against a known-bad provider."""
    from app.data import service

    breaker = _get_breaker()
    breaker.threshold = 3
    breaker.cooldown = timedelta(seconds=60)

    now = datetime.now(timezone.utc)

    class _FakeRow:
        def __init__(self, source, failure_count, last_failure_at):
            self.source = source
            self.failure_count = failure_count
            self.last_failure_at = last_failure_at

    class _AlwaysOpenSession:
        """Matches the is_open_batch SQL pattern: returns one OPEN row per
        requested source via ``.all()``."""

        async def execute(self, statement, params=None):
            sql = str(statement)
            if "MAX(failure_count)" in sql and "data_update_state" in sql:
                sources_param = (params or {}).get("sources", [])
                rows = [
                    _FakeRow(
                        source=s,
                        failure_count=5,
                        last_failure_at=now - timedelta(seconds=10),
                    )
                    for s in sources_param
                ]

                class _Result:
                    def all(self):
                        return rows

                return _Result()

            class _EmptyResult:
                def first(self):
                    return None

                def all(self):
                    return []

            return _EmptyResult()

    # fetch_with_fallback must NOT be called when all circuits are open.
    fetch_called = False

    def fake_fetch(*args, **kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("fetch_with_fallback must not be called when all circuits open")

    # stock_basic_providers() returns 3 providers by default (adata, baostock, akshare)
    monkeypatch.setattr(service, "fetch_with_fallback", fake_fetch)
    monkeypatch.setattr(service, "stock_basic_providers", lambda: [
        _FakeProvider("adata"),
        _FakeProvider("baostock"),
        _FakeProvider("akshare"),
    ])
    monkeypatch.setattr(service, "get_data_proxy_url", lambda: None)

    with pytest.raises(DataProviderError) as excinfo:
        await service.sync_stock_basic(_AlwaysOpenSession())

    assert "all providers circuit-open" in str(excinfo.value)
    assert "stock_basic" in str(excinfo.value)
    assert fetch_called is False  # breaker short-circuited before fetch


@pytest.mark.asyncio
async def test_sync_kline_short_circuits_when_all_circuits_open(monkeypatch):
    """When all providers' circuits are OPEN, sync_kline must:
    1. Call the breaker exactly ONCE (via is_open_batch) — not per-stock.
    2. NEVER call fetch_with_fallback — uses _no_provider_fetch shim instead.
    3. Surface the error for each ts_code (per-stock failure aggregation).
    """
    from app.data import service

    breaker = _get_breaker()
    breaker.threshold = 3
    breaker.cooldown = timedelta(seconds=60)

    now = datetime.now(timezone.utc)

    # Track how many DB queries fire against data_update_state (breaker table)
    # NOTE: ``record_update_failure`` does INSERT INTO data_update_state with
    # failure_count in the SQL — so we must match on the SELECT-specific
    # ``MAX(failure_count)`` to avoid double-counting writes.
    breaker_query_count = 0

    class _AlwaysOpenSession:
        async def execute(self, statement, params=None):
            nonlocal breaker_query_count
            sql = str(statement)
            # _bulk_load_failure_counts SELECT — has ``ts_code = ANY`` in the
            # WHERE clause. Return empty so no stock is skipped for prior
            # failures (this test is about circuit-open behavior, not the
            # failure-count skip path).
            if "MAX(failure_count)" in sql and "ts_code = ANY" in sql:
                class _EmptyResult:
                    def all(self):
                        return []
                return _EmptyResult()
            # breaker.is_open_batch SELECT — uniquely identified by MAX(failure_count)
            if "MAX(failure_count)" in sql and "data_update_state" in sql:
                breaker_query_count += 1
                class _Result:
                    def all(self):
                        # Return rows showing every provider is OPEN
                        return [
                            _FakeRow(
                                source="adata",
                                failure_count=5,
                                last_failure_at=now - timedelta(seconds=10),
                            ),
                            _FakeRow(
                                source="baostock",
                                failure_count=5,
                                last_failure_at=now - timedelta(seconds=10),
                            ),
                        ]
                return _Result()
            # ST bulk query — return empty (no ST stocks)
            if "is_st" in sql and "stock_basic" in sql:
                class _EmptyResult:
                    def mappings(self):
                        return self

                    def all(self):
                        return []
                return _EmptyResult()
            # Other queries — return empty
            class _EmptyResult:
                def first(self):
                    return None

                def scalar_one_or_none(self):
                    return None

                def all(self):
                    return []

                def mappings(self):
                    return self
            return _EmptyResult()

        async def commit(self):
            return None

        async def rollback(self):
            return None

    class _FakeRow:
        def __init__(self, source, failure_count, last_failure_at):
            self.source = source
            self.failure_count = failure_count
            self.last_failure_at = last_failure_at

    class _AlwaysOpenPerStockSession:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _AlwaysOpenSession()

        async def __aexit__(self, *a):
            return False

    fetch_called = False

    def fake_fetch(*args, **kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("fetch must not be called when all circuits open")

    monkeypatch.setattr(service, "fetch_with_fallback", fake_fetch)
    monkeypatch.setattr(service, "default_providers", lambda: [
        _FakeProvider("adata"),
        _FakeProvider("baostock"),
    ])
    monkeypatch.setattr(service, "get_data_proxy_url", lambda: None)

    from datetime import date as _date
    with pytest.raises(RuntimeError) as excinfo:
        await service.sync_kline(
            session=None,
            ts_codes=["000001.SZ", "000002.SZ", "000003.SZ"],
            start_date=_date(2026, 5, 1),
            end_date=_date(2026, 5, 2),
            commit_each=True,
            concurrency=2,
            session_factory=_AlwaysOpenPerStockSession,
        )

    # All 3 stocks failed (no providers available); error message should
    # mention the circuit-open condition.
    assert "circuit-open" in str(excinfo.value)
    # CRITICAL: fetch_with_fallback MUST NOT be called — the _no_provider_fetch
    # shim raises before any HTTP request is made.
    assert fetch_called is False
    # CRITICAL: breaker should be consulted ONCE (is_open_batch), not 3 times
    # (once per stock). 1 breaker query + 1 ST query = 2 queries against the
    # setup session — but the breaker query itself must fire exactly once.
    # Note: per_stock wk_sessions will also run other queries (record_update_failure
    # etc.), but those don't hit data_update_state with failure_count in SELECT.
    # We only count breaker SELECT queries.
    assert breaker_query_count == 1, (
        f"breaker should be queried exactly once via is_open_batch, got {breaker_query_count}"
    )


@pytest.mark.asyncio
async def test_sync_kline_loads_is_st_in_batch_not_per_stock(monkeypatch):
    """sync_kline must issue ONE _bulk_load_is_st query (covering all ts_codes)
    rather than N per-stock _is_st_stock queries.

    This test verifies the batched behaviour: even with 3 ts_codes, the
    is_st query fires exactly once (in setup), not 3 times (in process_code).
    """
    from app.data import service

    # Disable the breaker so it doesn't add queries
    breaker = _get_breaker()
    breaker.threshold = 0

    is_st_query_count = 0

    class _TrackingSession:
        async def execute(self, statement, params=None):
            nonlocal is_st_query_count
            sql = str(statement)
            if "is_st" in sql and "stock_basic" in sql:
                is_st_query_count += 1
                class _Result:
                    def mappings(self):
                        return self

                    def all(self):
                        return []
                return _Result()
            # Other queries — minimal stubs
            class _EmptyResult:
                def first(self):
                    return None

                def scalar_one_or_none(self):
                    return None

                def all(self):
                    return []

                def mappings(self):
                    return self
            return _EmptyResult()

        async def commit(self):
            return None

        async def rollback(self):
            return None

    class _PerStockSessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _TrackingSession()

        async def __aexit__(self, *a):
            return False

    # Make fetch_with_fallback return empty so process_code exits quickly
    # via the "no records returned" path.
    def fake_fetch(*args, **kwargs):
        raise DataProviderError("no records returned")

    monkeypatch.setattr(service, "fetch_with_fallback", fake_fetch)
    monkeypatch.setattr(service, "default_providers", lambda: [_FakeProvider("adata")])
    monkeypatch.setattr(service, "get_data_proxy_url", lambda: None)

    from datetime import date as _date
    # sync_kline raises RuntimeError when all stocks fail (total==0 + failures).
    # The is_st_query_count assertion runs AFTER the exception is raised, so
    # we still verify the batched load happened exactly once.
    with pytest.raises(RuntimeError, match="all kline sync attempts failed"):
        await service.sync_kline(
            session=None,
            ts_codes=["000001.SZ", "000002.SZ", "000003.SZ"],
            start_date=_date(2026, 5, 1),
            end_date=_date(2026, 5, 2),
            commit_each=True,
            concurrency=1,  # serial to make query counting deterministic
            session_factory=_PerStockSessionFactory,
        )

    # CRITICAL: is_st query must fire EXACTLY ONCE (in bulk_load_is_st at
    # sync_kline entry), not 3 times (once per stock).
    assert is_st_query_count == 1, (
        f"_bulk_load_is_st should fire exactly once, got {is_st_query_count}"
    )


# ---------------------------------------------------------------------------
# Original breaker.is_open behavior (regression coverage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breaker_is_open_returns_false_when_no_row():
    """breaker.is_open returns False when no row exists for (data_type, source)."""
    breaker = CircuitBreaker(threshold=3, cooldown_seconds=60)

    class _EmptySession:
        async def execute(self, statement, params=None):
            class _Result:
                def first(self):
                    return None
            return _Result()

    assert await breaker.is_open(_EmptySession(), "daily_kline", "adata") is False


@pytest.mark.asyncio
async def test_breaker_is_open_returns_false_when_below_threshold():
    """breaker.is_open returns False when failure_count < threshold."""
    breaker = CircuitBreaker(threshold=5, cooldown_seconds=60)
    now = datetime.now(timezone.utc)

    class _Session:
        async def execute(self, statement, params=None):
            class _Result:
                def first(self):
                    return _FakeResult(
                        failure_count=2,  # below threshold
                        last_failure_at=now - timedelta(seconds=10),
                    )
            return _Result()

    assert await breaker.is_open(_Session(), "daily_kline", "adata") is False


@pytest.mark.asyncio
async def test_breaker_is_open_returns_true_when_above_threshold_in_cooldown():
    """breaker.is_open returns True when failure_count >= threshold AND
    last_failure_at is within the cooldown window."""
    breaker = CircuitBreaker(threshold=5, cooldown_seconds=60)
    now = datetime.now(timezone.utc)

    class _Session:
        async def execute(self, statement, params=None):
            class _Result:
                def first(self):
                    return _FakeResult(
                        failure_count=10,  # well above threshold
                        last_failure_at=now - timedelta(seconds=10),  # within cooldown
                    )
            return _Result()

    assert await breaker.is_open(_Session(), "daily_kline", "adata") is True


@pytest.mark.asyncio
async def test_breaker_is_open_returns_false_after_cooldown_elapsed():
    """breaker.is_open returns False (half-open) when cooldown has elapsed,
    even if failure_count is above threshold."""
    breaker = CircuitBreaker(threshold=5, cooldown_seconds=60)
    now = datetime.now(timezone.utc)

    class _Session:
        async def execute(self, statement, params=None):
            class _Result:
                def first(self):
                    return _FakeResult(
                        failure_count=10,
                        last_failure_at=now - timedelta(seconds=120),  # > 60s cooldown
                    )
            return _Result()

    assert await breaker.is_open(_Session(), "daily_kline", "adata") is False
