from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_redis_health_ok() -> None:
    """``GET /api/health/redis`` returns 200 + reachable when Redis ping succeeds."""
    fake_client = AsyncMock()
    fake_client.ping = AsyncMock(return_value=True)
    fake_client.aclose = AsyncMock(return_value=None)

    with patch("redis.asyncio.from_url", return_value=fake_client):
        client = TestClient(app)
        response = client.get("/api/health/redis")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "redis": "reachable"}
    fake_client.ping.assert_awaited_once()
    fake_client.aclose.assert_awaited_once()


def test_redis_health_503_on_failure() -> None:
    """``GET /api/health/redis`` returns 503 when Redis ping raises."""
    from redis.exceptions import RedisError

    fake_client = AsyncMock()
    fake_client.ping = AsyncMock(side_effect=RedisError("connection refused"))
    fake_client.aclose = AsyncMock(return_value=None)

    with patch("redis.asyncio.from_url", return_value=fake_client):
        client = TestClient(app)
        response = client.get("/api/health/redis")

    assert response.status_code == 503
    assert response.json()["detail"].startswith("redis unavailable")
    # aclose() must still be called via finally to avoid leaking the client
    fake_client.aclose.assert_awaited_once()


def test_aggregate_health_degraded_when_redis_down(monkeypatch) -> None:
    """``GET /api/health`` returns ``status: degraded`` when Redis is unreachable
    but DB is up. This is the core P1 H-5 scenario: Docker healthcheck must
    fail (via 503 → unhealthy) when Redis (WS / BeatLock / event push) is down,
    even if DB is still reachable.
    """
    from app import main as main_module
    from app.db.session import get_session

    class _FakeSession:
        async def execute(self, statement, params=None):
            assert "SELECT 1" in str(statement)
            return object()  # _ping_db doesn't read the result

        async def close(self):
            return None

    fake_session = _FakeSession()

    async def override_session():
        yield fake_session

    async def fake_ping_redis_ok():
        return "ok"

    async def fake_ping_redis_fail():
        raise RuntimeError("redis down")

    monkeypatch.setattr(main_module, "_ping_redis", fake_ping_redis_fail)
    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"db": "ok", "redis": "fail"}

    # Sanity: when Redis is healthy, status flips back to ok
    monkeypatch.setattr(main_module, "_ping_redis", fake_ping_redis_ok)
    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/api/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok"}}
