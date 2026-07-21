"""Circuit breaker for data providers.

Reads `data_update_state.failure_count` (already maintained by repository.record_update_failure/success)
and short-circuits fetch attempts when a provider has failed too many times in a row.

Open state lasts `cooldown_seconds` since the most recent failure; once expired, the
provider is given another chance (half-open). A successful fetch resets failure_count=0
via record_update_success, which immediately closes the circuit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Per-(data_type, source) breaker backed by data_update_state.failure_count."""

    def __init__(
        self,
        threshold: int | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.threshold = threshold if threshold is not None else settings.circuit_breaker_threshold
        self.cooldown = timedelta(
            seconds=cooldown_seconds if cooldown_seconds is not None else settings.circuit_breaker_cooldown_seconds
        )

    async def is_open(self, session: AsyncSession, data_type: str, source: str) -> bool:
        """True if `source` has hit failure_count >= threshold within the cooldown window."""
        if self.threshold <= 0:
            return False
        row = (await session.execute(
            text(
                "SELECT failure_count, last_failure_at "
                "FROM data_update_state "
                "WHERE data_type = :dt AND source = :s"
            ),
            {"dt": data_type, "s": source},
        )).first()
        if row is None:
            return False
        if row.failure_count is None or row.failure_count < self.threshold:
            return False
        last_failure = row.last_failure_at
        if last_failure is None:
            return False
        # Cooldown not yet elapsed → still open
        if datetime.now(timezone.utc) - last_failure.replace(tzinfo=timezone.utc) < self.cooldown:
            return True
        # Cooldown expired → half-open (allow one attempt)
        return False

    async def record_success(self, session: AsyncSession, data_type: str, source: str) -> None:
        """Delegate to repository.record_update_success — closes the circuit."""
        # Intentionally a thin wrapper; the repository function does the UPSERT.
        # Called by fetcher when a provider succeeds.
        pass  # No-op: fetcher already calls record_update_success

    async def record_failure(self, session: AsyncSession, data_type: str, source: str, error: str) -> None:
        """Delegate to repository.record_update_failure — increments failure_count."""
        pass  # No-op: fetcher already calls record_update_failure
