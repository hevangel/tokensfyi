"""Source-scoped rate limiting middleware for submission and report endpoints.

Implements Requirement 10.10: IF one network source exceeds the configured
Rate_Limit for submissions or Community_Reports, THE Token_Budget_Platform
SHALL reject excess requests with a positive retry interval consistent with
the configured window.

Uses keyed network-source pseudonyms (not raw IPs) and deterministic windows.
Returns safe 429 responses with a positive Retry-After header while leaving
other sources completely unaffected.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from token_budget_platform.domain.rate_limiting import (
    RateLimitConfig,
    compute_network_pseudonym,
    compute_window_start,
)
from token_budget_platform.transports.api.problems import rate_limited

# Default rate limit configurations
DEFAULT_SUBMISSION_LIMIT = RateLimitConfig(max_requests=10, window_seconds=3600)
DEFAULT_REPORT_LIMIT = RateLimitConfig(max_requests=5, window_seconds=3600)

# URL patterns that are rate-limited (matched against request.path)
SUBMISSION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^/api/v1/submission-drafts/?$"),
]
REPORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^/api/v1/records/[^/]+/reports/?$"),
]


def _get_rate_limit_key() -> bytes:
    """Retrieve the HMAC key used for network-source pseudonym derivation."""
    key = getattr(settings, "RATE_LIMIT_HMAC_KEY", None)
    if key:
        if isinstance(key, str):
            return key.encode("utf-8")
        return bytes(key)
    # Fall back to SECRET_KEY for local development
    return settings.SECRET_KEY.encode("utf-8")


def _extract_network_source(request: HttpRequest) -> str:
    """Extract the network source identifier from the request.

    Checks X-Forwarded-For (set by ALB/CloudFront) first, then falls back
    to REMOTE_ADDR.  Returns the leftmost forwarded address (the client).
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        # Take the leftmost (client) address
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _match_scope(path: str, method: str) -> str | None:
    """Determine which rate limit scope applies to this request, if any.

    Only POST requests to submission/report endpoints are rate-limited.
    """
    if method != "POST":
        return None

    for pattern in SUBMISSION_PATTERNS:
        if pattern.match(path):
            return "submission"

    for pattern in REPORT_PATTERNS:
        if pattern.match(path):
            return "report"

    return None


def _get_config_for_scope(scope: str) -> RateLimitConfig:
    """Return the rate limit config for a given scope, using settings overrides."""
    if scope == "submission":
        max_req = getattr(
            settings, "RATE_LIMIT_SUBMISSION_MAX", DEFAULT_SUBMISSION_LIMIT.max_requests
        )
        window = getattr(
            settings, "RATE_LIMIT_SUBMISSION_WINDOW", DEFAULT_SUBMISSION_LIMIT.window_seconds
        )
        return RateLimitConfig(max_requests=max_req, window_seconds=window)
    elif scope == "report":
        max_req = getattr(settings, "RATE_LIMIT_REPORT_MAX", DEFAULT_REPORT_LIMIT.max_requests)
        window = getattr(settings, "RATE_LIMIT_REPORT_WINDOW", DEFAULT_REPORT_LIMIT.window_seconds)
        return RateLimitConfig(max_requests=max_req, window_seconds=window)
    # Defensive fallback
    return DEFAULT_SUBMISSION_LIMIT


class SourceScopedRateLimitMiddleware:
    """Django middleware that enforces per-source rate limits.

    Only applies to POST requests on configured submission/report URL patterns.
    Uses the configured RateLimitStore (defaulting to PostgreSQL in production
    or in-memory for tests).

    When a source exceeds its limit, returns a 429 response with:
    - application/problem+json body
    - Retry-After header with positive seconds until window expiry
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._store: Any = None

    def _get_store(self) -> Any:
        """Lazy-load the rate limit store to avoid import cycles at startup."""
        if self._store is None:
            store = getattr(settings, "RATE_LIMIT_STORE", None)
            if store is not None:
                self._store = store
            else:
                from token_budget_platform.adapters.rate_limiting import PostgresRateLimitStore

                self._store = PostgresRateLimitStore()
        return self._store

    def __call__(self, request: HttpRequest) -> HttpResponse:
        method = request.method or "GET"
        scope = _match_scope(request.path, method)
        if scope is None:
            return self.get_response(request)

        config = _get_config_for_scope(scope)
        raw_source = _extract_network_source(request)
        key = _get_rate_limit_key()
        pseudonym = compute_network_pseudonym(raw_source, key=key)

        # Import clock from settings or use system time
        clock = getattr(settings, "RATE_LIMIT_CLOCK", None)
        if clock is not None:
            now = clock.now()
        else:
            from datetime import datetime

            now = datetime.now(UTC)

        window_start = compute_window_start(now, config.window_seconds)
        store = self._get_store()

        result = store.increment_and_check(
            pseudonym=pseudonym,
            scope=scope,
            window_start=window_start,
            window_seconds=config.window_seconds,
            max_requests=config.max_requests,
        )

        if not result.allowed:
            correlation_id = request.META.get("HTTP_X_CORRELATION_ID")
            return rate_limited(
                retry_after=result.retry_after,
                correlation_id=correlation_id,
            )

        return self.get_response(request)
