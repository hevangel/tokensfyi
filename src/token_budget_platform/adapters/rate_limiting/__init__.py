"""Rate limiting adapters: PostgreSQL-backed and in-memory for tests."""

from __future__ import annotations

from datetime import UTC, datetime

from django.db import connection

from token_budget_platform.domain.rate_limiting import (
    RateLimitConfig as RateLimitConfig,
)
from token_budget_platform.domain.rate_limiting import (
    RateLimitResult,
    compute_retry_after,
)


class PostgresRateLimitStore:
    """PostgreSQL-backed rate limit counter using INSERT ... ON CONFLICT.

    Uses a single table with upsert semantics so that concurrent requests from
    the same source are safely serialized through the database.  No Redis
    dependency is required.
    """

    def increment_and_check(
        self,
        *,
        pseudonym: str,
        scope: str,
        window_start: datetime,
        window_seconds: int,
        max_requests: int,
    ) -> RateLimitResult:
        """Atomically increment and check using PostgreSQL upsert."""
        now = datetime.now(UTC)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO rate_limit_counter (pseudonym, scope, window_start, request_count)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (pseudonym, scope, window_start)
                DO UPDATE SET request_count = rate_limit_counter.request_count + 1
                RETURNING request_count
                """,
                [
                    pseudonym,
                    scope,
                    window_start,
                ],
            )
            row = cursor.fetchone()
            count = row[0] if row else 1

        if count <= max_requests:
            return RateLimitResult(allowed=True, retry_after=0)

        retry_after = compute_retry_after(now, window_seconds)
        return RateLimitResult(allowed=False, retry_after=retry_after)


class InMemoryRateLimitStore:
    """In-memory rate limit store for unit tests and local development.

    Provides the same interface as the PostgreSQL store but keeps counters
    in a dictionary keyed by (pseudonym, scope, window_start).
    """

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str, datetime], int] = {}

    def increment_and_check(
        self,
        *,
        pseudonym: str,
        scope: str,
        window_start: datetime,
        window_seconds: int,
        max_requests: int,
    ) -> RateLimitResult:
        key = (pseudonym, scope, window_start)
        current = self._counters.get(key, 0) + 1
        self._counters[key] = current

        if current <= max_requests:
            return RateLimitResult(allowed=True, retry_after=0)

        now = datetime.now(UTC)
        retry_after = compute_retry_after(now, window_seconds)
        return RateLimitResult(allowed=False, retry_after=retry_after)

    def reset(self) -> None:
        """Clear all counters (useful between tests)."""
        self._counters.clear()
