from decimal import Decimal

import pytest

from app.api import preferences
from app.preferences import service


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class CaptureSession:
    def __init__(self, results=None):
        self.statements = []
        self.params = []
        self.commits = 0
        self.results = list(results or [])

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        if self.results:
            return self.results.pop(0)
        return FakeResult([])

    async def commit(self):
        self.commits += 1


class FakeRequest:
    headers = {"X-User-ID": "1"}
    query_params = {}


@pytest.mark.asyncio
async def test_get_trading_fee_returns_defaults_when_preference_missing() -> None:
    session = CaptureSession([FakeResult([])])

    result = await service.get_trading_fee_payload(session, 1)

    assert result == {
        "commission_rate": "0.00025",
        "min_commission": "5.0",
        "stamp_tax_rate": "0.0005",
        "transfer_fee_rate": "0.00001",
        "waive_min_commission": False,
    }


@pytest.mark.asyncio
async def test_save_trading_fee_then_get_returns_same_values() -> None:
    payload = {
        "commission_rate": Decimal("0.0003"),
        "min_commission": Decimal("4.0"),
        "waive_min_commission": True,
        "stamp_tax_rate": Decimal("0.0006"),
        "transfer_fee_rate": Decimal("0.00002"),
    }
    session = CaptureSession()

    saved = await service.save_trading_fee_payload(session, user_id=1, payload=payload)
    get_session = CaptureSession([FakeResult([{"value": saved}])])
    loaded = await service.get_trading_fee_payload(get_session, 1)

    assert loaded == saved
    assert session.commits == 1
    assert session.params[0]["user_id"] == 1
    assert session.params[0]["key"] == "trading_fee"


@pytest.mark.asyncio
async def test_trading_fee_api_uses_request_user_id() -> None:
    session = CaptureSession([FakeResult([])])

    result = await preferences.get_trading_fee(FakeRequest(), session)

    assert result["commission_rate"] == "0.00025"
    assert session.params[0]["user_id"] == 1


@pytest.mark.asyncio
async def test_get_kline_sync_returns_default_when_preference_missing() -> None:
    session = CaptureSession([FakeResult([])])

    result = await service.get_kline_sync_payload(session)

    assert result == {"full_kline_sync_concurrency": 2}
    assert session.params[0]["user_id"] == 0
    assert session.params[0]["key"] == "kline_sync"


@pytest.mark.asyncio
async def test_save_kline_sync_uses_global_preference_key() -> None:
    session = CaptureSession()

    result = await service.save_kline_sync_payload(session, {"full_kline_sync_concurrency": 6})

    assert result == {"full_kline_sync_concurrency": 6}
    assert session.commits == 1
    assert session.params[0]["user_id"] == 0
    assert session.params[0]["key"] == "kline_sync"


@pytest.mark.asyncio
async def test_kline_sync_api_accepts_concurrency_alias() -> None:
    session = CaptureSession()
    request = preferences.KlineSyncPreference(concurrency=4)

    result = await preferences.update_kline_sync(request, session)

    assert result == {"full_kline_sync_concurrency": 4}
