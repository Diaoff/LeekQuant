import pytest

from app.data.source_service import check_source, check_sources, list_sources, save_sources

pytestmark = pytest.mark.asyncio


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeSourceSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements = []
        self.params = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        if "SELECT id, name, display_name, priority, enabled" in str(statement):
            return FakeResult(self.rows)
        return FakeResult([])

    async def commit(self):
        self.commits += 1


async def test_list_sources_merges_registered_plugins_with_db_config() -> None:
    session = FakeSourceSession(
        [
            {"id": 1, "name": "adata", "display_name": "AData", "priority": 1, "enabled": True},
            {"id": 2, "name": "unknown_old", "display_name": "Old", "priority": 2, "enabled": True},
        ]
    )

    sources = await list_sources(session)
    names = [source["name"] for source in sources]

    assert names[0] == "adata"
    assert "unknown_old" not in names
    assert "eastmoney_http" in names
    assert "capabilities" in sources[0]


async def test_save_sources_persists_registered_plugins_only(monkeypatch) -> None:
    import app.data.source_service as source_service

    configured = []
    monkeypatch.setattr(source_service, "configure_providers", lambda names: configured.extend(names))
    session = FakeSourceSession(
        [
            {"id": 1, "name": "eastmoney_http", "display_name": "EastMoney HTTP", "priority": 1, "enabled": True},
            {"id": 2, "name": "tencent_http", "display_name": "Tencent Finance HTTP", "priority": 2, "enabled": False},
        ]
    )

    result = await save_sources(
        session,
        [
            {"name": "eastmoney_http", "display_name": "EastMoney HTTP", "enabled": True},
            {"name": "tencent_http", "display_name": "Tencent Finance HTTP", "enabled": False},
            {"name": "missing", "display_name": "Missing", "enabled": True},
        ],
    )

    assert configured == ["eastmoney_http"]
    assert any("DELETE FROM data_source_config" in statement for statement in session.statements)
    assert [params["name"] for params in session.params if "name" in params] == ["eastmoney_http", "tencent_http"]
    assert result[0]["name"] == "eastmoney_http"


async def test_check_source_uses_first_successful_declared_capability(monkeypatch) -> None:
    import app.data.source_service as source_service

    class FakeProvider:
        name = "checkable"
        display_name = "Checkable"
        capabilities = frozenset({"daily_kline", "fundamentals"})
        priority_default = 99

        def fetch_daily_kline(self, *_args):
            return []

        def fetch_stock_fundamentals(self, *_args):
            return [object()]

    monkeypatch.setitem(source_service.PROVIDER_REGISTRY, "checkable", FakeProvider)

    result = await check_source("checkable")

    assert result["ok"] is True
    assert result["checked_capability"] == "fundamentals"
    assert result["records"] == 1
    assert result["error"] is None


async def test_check_source_reports_unknown_source() -> None:
    result = await check_source("missing_source")

    assert result["ok"] is False
    assert result["name"] == "missing_source"
    assert "unknown source name" in result["error"]


async def test_check_sources_checks_requested_names(monkeypatch) -> None:
    import app.data.source_service as source_service

    async def fake_check_source(name):
        return {"name": name, "ok": True}

    monkeypatch.setattr(source_service, "check_source", fake_check_source)

    assert await check_sources(["a", "b"]) == [{"name": "a", "ok": True}, {"name": "b", "ok": True}]
