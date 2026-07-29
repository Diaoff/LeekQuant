"""Tests for BeatLock distributed lock and decorator (P1-10)."""
from __future__ import annotations

import pytest
from redis.exceptions import RedisError

from app.tasks import beat_lock as beat_lock_module
from app.tasks.beat_lock import (
    BeatLock,
    BeatLockSkipped,
    get_beat_lock,
    reset_beat_lock_for_tests,
    with_beat_lock,
)


class FakeLuaScript:
    """Fake of redis-py Script object — simulates the safe-release logic."""

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store
        self.calls: list[dict] = []

    def __call__(self, *, keys: list[str], args: list[str]) -> int:
        self.calls.append({"keys": keys, "args": args})
        key, owner = keys[0], args[0]
        if self._store.get(key) == owner:
            self._store.pop(key, None)
            return 1
        return 0


class FakeSyncRedis:
    """Fake sync redis client backing BeatLock — supports only SET NX EX + DEL."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[dict] = []
        self.fail_next_set: bool = False
        self.fail_next_release: bool = False

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if self.fail_next_set:
            self.fail_next_set = False
            raise RedisError("simulated acquire failure")
        if nx:
            if key in self.store:
                return None
            self.store[key] = value
            return True
        self.store[key] = value
        return True

    def register_script(self, _source: str) -> FakeLuaScript:
        return FakeLuaScript(self.store)

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure each test starts with a fresh BeatLock singleton."""
    reset_beat_lock_for_tests()
    yield
    reset_beat_lock_for_tests()


def _make_beat_lock() -> tuple[BeatLock, FakeSyncRedis]:
    """Construct a BeatLock backed by a FakeSyncRedis (no real Redis)."""
    fake = FakeSyncRedis()
    lock = BeatLock.__new__(BeatLock)
    lock._redis_url = "redis://fake/0"
    lock._ttl = 1860
    lock._client = fake
    lock._release_script = fake.register_script("")
    return lock, fake


def test_acquire_succeeds_when_lock_free():
    lock, fake = _make_beat_lock()
    assert lock.acquire("task_a") is True
    assert "beat:lock:task_a" in fake.store
    # SET NX EX must be used
    assert fake.set_calls[-1]["nx"] is True
    assert fake.set_calls[-1]["ex"] == 1860


def test_acquire_fails_when_lock_held():
    lock, fake = _make_beat_lock()
    assert lock.acquire("task_a") is True
    # Second acquire (different BeatLock instance simulating a second worker)
    lock2, _ = _make_beat_lock()
    lock2._client = fake  # share the same fake store
    lock2._release_script = FakeLuaScript(fake.store)
    assert lock2.acquire("task_a") is False


def test_release_clears_key_when_owner_matches():
    lock, fake = _make_beat_lock()
    lock.acquire("task_a")
    lock.release("task_a")
    assert "beat:lock:task_a" not in fake.store


def test_release_no_op_when_owner_differs():
    lock, fake = _make_beat_lock()
    lock.acquire("task_a")
    # Simulate another worker overwriting our key (e.g. after TTL expiry)
    fake.store["beat:lock:task_a"] = "other-worker-id"
    lock.release("task_a")
    # Key is NOT deleted because we don't own it
    assert fake.store["beat:lock:task_a"] == "other-worker-id"


def test_acquire_fails_open_on_redis_error():
    lock, fake = _make_beat_lock()
    fake.fail_next_set = True
    # Fail-open: returns True even though Redis errored
    assert lock.acquire("task_b") is True


def test_release_swallows_redis_error():
    lock, fake = _make_beat_lock()
    lock.acquire("task_c")

    class FailingScript:
        def __call__(self, *, keys, args):
            raise RedisError("simulated release failure")

    lock._release_script = FailingScript()
    # Should not raise
    lock.release("task_c")


def test_decorator_runs_task_when_lock_acquired():
    lock, _fake = _make_beat_lock()
    beat_lock_module._beat_lock = lock

    @with_beat_lock("test.task")
    def task():
        return {"ran": True}

    assert task() == {"ran": True}


def test_decorator_skips_task_when_lock_held():
    lock, fake = _make_beat_lock()
    # Pre-acquire the lock to simulate another worker holding it
    fake.store["beat:lock:test.held"] = "another-worker"
    beat_lock_module._beat_lock = lock

    @with_beat_lock("test.held")
    def task():
        raise AssertionError("task should not run when lock is held")

    # Must raise BeatLockSkipped (not return None): returning None used to make
    # the Celery success signal wrongly mark the row "success" (phantom success).
    with pytest.raises(BeatLockSkipped):
        task()


def test_decorator_marks_cancelled_when_lock_held(monkeypatch):
    """When the lock is held, the skipped run's task_runs row is marked cancelled."""
    lock, fake = _make_beat_lock()
    fake.store["beat:lock:test.cancelled"] = "another-worker"
    beat_lock_module._beat_lock = lock

    cancelled: dict = {}

    async def fake_mark_cancelled(session, *, task_id, error_message):
        cancelled["task_id"] = task_id
        cancelled["error_message"] = error_message

    def fake_session_cm():
        class _S:
            async def __aenter__(self):
                return _S

            async def __aexit__(self, *_e):
                return False

        return _S()

    monkeypatch.setattr(
        "app.data.repository.mark_task_run_cancelled", fake_mark_cancelled
    )
    monkeypatch.setattr("app.db.session.async_session_factory", fake_session_cm)

    # A bound Celery task's first positional arg carries request.id.
    class _FakeRequest:
        id = "task-cancelled-1"

    class _FakeSelf:
        request = _FakeRequest()

    @with_beat_lock("test.cancelled")
    def task(*_args):
        raise AssertionError("should not run")

    with pytest.raises(BeatLockSkipped):
        task(_FakeSelf())
    assert cancelled["task_id"] == "task-cancelled-1"
    assert "beat lock" in (cancelled["error_message"] or "")


def test_decorator_releases_lock_after_success():
    lock, fake = _make_beat_lock()
    beat_lock_module._beat_lock = lock

    @with_beat_lock("test.release_ok")
    def task():
        return 42

    task()
    assert "beat:lock:test.release_ok" not in fake.store


def test_decorator_releases_lock_after_exception():
    lock, fake = _make_beat_lock()
    beat_lock_module._beat_lock = lock

    @with_beat_lock("test.release_err")
    def task():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        task()
    # Lock must be released even on exception
    assert "beat:lock:test.release_err" not in fake.store


def test_get_beat_lock_returns_singleton():
    a = get_beat_lock()
    b = get_beat_lock()
    assert a is b


def test_reset_beat_lock_for_tests_clears_singleton():
    a = get_beat_lock()
    reset_beat_lock_for_tests()
    b = get_beat_lock()
    assert a is not b


def test_default_ttl_is_1860():
    """DEFAULT_TTL_SECONDS must exceed the 1800s task_time_limit."""
    assert beat_lock_module.DEFAULT_TTL_SECONDS == 1860


def test_settings_default_ttl_is_1860():
    """Settings must default BEAT_LOCK_TTL_SECONDS to 1860."""
    from app.core.config import Settings

    settings = Settings(DATABASE_URL="postgresql+asyncpg://u:p@localhost/db")
    assert settings.beat_lock_ttl_seconds == 1860


def test_ttl_overridden_from_settings(monkeypatch):
    """BeatLock.__init__ reads settings.beat_lock_ttl_seconds when ttl_seconds is None."""
    from app.core.config import Settings

    test_settings = Settings(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost/db",
        BEAT_LOCK_TTL_SECONDS=999,
    )
    monkeypatch.setattr(
        "app.tasks.beat_lock.get_settings",
        lambda: test_settings,
    )

    # Construct BeatLock without explicit ttl_seconds — must read 999 from settings.
    # Avoid calling __init__'s redis client construction by setting up only the
    # fields we need; alternatively, patch redis_sync.from_url.
    monkeypatch.setattr(
        "app.tasks.beat_lock.redis_sync.from_url",
        lambda *_args, **_kwargs: FakeSyncRedis(),
    )
    lock = BeatLock()
    assert lock._ttl == 999


def test_ttl_explicit_param_overrides_settings(monkeypatch):
    """Explicit ttl_seconds param must take precedence over settings."""
    from app.core.config import Settings

    test_settings = Settings(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost/db",
        BEAT_LOCK_TTL_SECONDS=999,
    )
    monkeypatch.setattr(
        "app.tasks.beat_lock.get_settings",
        lambda: test_settings,
    )
    monkeypatch.setattr(
        "app.tasks.beat_lock.redis_sync.from_url",
        lambda *_args, **_kwargs: FakeSyncRedis(),
    )
    lock = BeatLock(ttl_seconds=777)
    assert lock._ttl == 777
