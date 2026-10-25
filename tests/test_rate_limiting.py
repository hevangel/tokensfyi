"""Tests for source-scoped rate limiting (Requirement 10.10).

Validates that:
- Keyed network-source pseudonyms are deterministic and non-reversible
- Deterministic windows align to UTC epoch
- Rate limiting rejects excess requests per source with positive Retry-After
- Other sources remain unaffected when one is rate-limited
- 429 responses use application/problem+json format
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from token_budget_platform.adapters.rate_limiting import InMemoryRateLimitStore
from token_budget_platform.domain.rate_limiting import (
    RateLimitConfig,
    compute_network_pseudonym,
    compute_retry_after,
    compute_window_start,
)


class TestRateLimitConfig:
    def test_valid_config(self) -> None:
        config = RateLimitConfig(max_requests=10, window_seconds=3600)
        assert config.max_requests == 10
        assert config.window_seconds == 3600

    def test_zero_max_requests_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_requests must be at least 1"):
            RateLimitConfig(max_requests=0, window_seconds=3600)

    def test_negative_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="window_seconds must be at least 1"):
            RateLimitConfig(max_requests=5, window_seconds=0)


class TestNetworkPseudonym:
    def test_deterministic_for_same_input(self) -> None:
        key = b"test-secret-key"
        source = "192.168.1.100"
        p1 = compute_network_pseudonym(source, key=key)
        p2 = compute_network_pseudonym(source, key=key)
        assert p1 == p2

    def test_different_sources_produce_different_pseudonyms(self) -> None:
        key = b"test-secret-key"
        p1 = compute_network_pseudonym("192.168.1.100", key=key)
        p2 = compute_network_pseudonym("192.168.1.101", key=key)
        assert p1 != p2

    def test_different_keys_produce_different_pseudonyms(self) -> None:
        source = "192.168.1.100"
        p1 = compute_network_pseudonym(source, key=b"key-one")
        p2 = compute_network_pseudonym(source, key=b"key-two")
        assert p1 != p2

    def test_pseudonym_has_prefix(self) -> None:
        p = compute_network_pseudonym("10.0.0.1", key=b"key")
        assert p.startswith("nps:")

    def test_raw_ip_not_in_pseudonym(self) -> None:
        source = "192.168.1.100"
        p = compute_network_pseudonym(source, key=b"secret")
        assert source not in p


class TestWindowComputation:
    def test_window_aligns_to_epoch(self) -> None:
        # 3600-second window: timestamps within the same hour map to same start
        t1 = datetime(2024, 1, 15, 10, 15, 30, tzinfo=UTC)
        t2 = datetime(2024, 1, 15, 10, 45, 0, tzinfo=UTC)
        start1 = compute_window_start(t1, 3600)
        start2 = compute_window_start(t2, 3600)
        assert start1 == start2
        assert start1 == datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

    def test_different_windows_produce_different_starts(self) -> None:
        t1 = datetime(2024, 1, 15, 10, 59, 59, tzinfo=UTC)
        t2 = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)
        start1 = compute_window_start(t1, 3600)
        start2 = compute_window_start(t2, 3600)
        assert start1 != start2

    def test_retry_after_is_positive(self) -> None:
        now = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        retry = compute_retry_after(now, 3600)
        assert retry > 0
        # 30 minutes remain in this hour window
        assert retry == 1800

    def test_retry_after_at_boundary_is_positive(self) -> None:
        # At exact window boundary, should return full window (at least 1)
        now = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)
        retry = compute_retry_after(now, 3600)
        assert retry >= 1


class TestInMemoryRateLimitStore:
    def test_allows_requests_within_limit(self) -> None:
        store = InMemoryRateLimitStore()
        window_start = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        for _ in range(5):
            result = store.increment_and_check(
                pseudonym="nps:abc123",
                scope="submission",
                window_start=window_start,
                window_seconds=3600,
                max_requests=5,
            )
            assert result.allowed is True

    def test_rejects_excess_requests(self) -> None:
        store = InMemoryRateLimitStore()
        window_start = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        # Exhaust the limit
        for _ in range(3):
            store.increment_and_check(
                pseudonym="nps:abc123",
                scope="submission",
                window_start=window_start,
                window_seconds=3600,
                max_requests=3,
            )

        # 4th request should be rejected
        result = store.increment_and_check(
            pseudonym="nps:abc123",
            scope="submission",
            window_start=window_start,
            window_seconds=3600,
            max_requests=3,
        )
        assert result.allowed is False
        assert result.retry_after > 0

    def test_other_sources_unaffected(self) -> None:
        store = InMemoryRateLimitStore()
        window_start = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        # Source A exhausts its limit
        for _ in range(4):
            store.increment_and_check(
                pseudonym="nps:source_a",
                scope="submission",
                window_start=window_start,
                window_seconds=3600,
                max_requests=3,
            )

        # Source B should still be allowed
        result = store.increment_and_check(
            pseudonym="nps:source_b",
            scope="submission",
            window_start=window_start,
            window_seconds=3600,
            max_requests=3,
        )
        assert result.allowed is True

    def test_different_scopes_tracked_independently(self) -> None:
        store = InMemoryRateLimitStore()
        window_start = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        # Exhaust submission limit
        for _ in range(4):
            store.increment_and_check(
                pseudonym="nps:same_source",
                scope="submission",
                window_start=window_start,
                window_seconds=3600,
                max_requests=3,
            )

        # Report scope should still be allowed
        result = store.increment_and_check(
            pseudonym="nps:same_source",
            scope="report",
            window_start=window_start,
            window_seconds=3600,
            max_requests=5,
        )
        assert result.allowed is True

    def test_new_window_resets_counter(self) -> None:
        store = InMemoryRateLimitStore()
        window1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        window2 = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        # Exhaust limit in window 1
        for _ in range(4):
            store.increment_and_check(
                pseudonym="nps:abc123",
                scope="submission",
                window_start=window1,
                window_seconds=3600,
                max_requests=3,
            )

        # New window should allow again
        result = store.increment_and_check(
            pseudonym="nps:abc123",
            scope="submission",
            window_start=window2,
            window_seconds=3600,
            max_requests=3,
        )
        assert result.allowed is True


class TestMiddlewareIntegration:
    """Test the middleware via Django's test client."""

    @pytest.fixture
    def store(self) -> InMemoryRateLimitStore:
        return InMemoryRateLimitStore()

    def test_non_rate_limited_paths_pass_through(self, settings: Any) -> None:
        """GET requests and non-matching paths are never rate-limited."""

        from token_budget_platform.transports.middleware.rate_limit import (
            _match_scope,
        )

        assert _match_scope("/api/v1/records", "GET") is None
        assert _match_scope("/api/v1/health/live", "GET") is None
        assert _match_scope("/api/v1/health/ready", "GET") is None
        assert _match_scope("/api/v1/submission-drafts", "GET") is None

    def test_submission_post_matches_scope(self) -> None:
        from token_budget_platform.transports.middleware.rate_limit import _match_scope

        assert _match_scope("/api/v1/submission-drafts", "POST") == "submission"
        assert _match_scope("/api/v1/submission-drafts/", "POST") == "submission"

    def test_report_post_matches_scope(self) -> None:
        from token_budget_platform.transports.middleware.rate_limit import _match_scope

        assert _match_scope("/api/v1/records/some-uuid/reports", "POST") == "report"
        assert _match_scope("/api/v1/records/some-uuid/reports/", "POST") == "report"

    def test_middleware_returns_429_with_retry_after(self, settings: Any) -> None:
        """When rate limit is exceeded, response is 429 with Retry-After."""
        from django.http import HttpResponse
        from django.test import RequestFactory

        from token_budget_platform.adapters.clock import FrozenClock
        from token_budget_platform.adapters.rate_limiting import InMemoryRateLimitStore
        from token_budget_platform.transports.middleware.rate_limit import (
            SourceScopedRateLimitMiddleware,
        )

        store = InMemoryRateLimitStore()
        clock = FrozenClock(datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC))

        settings.RATE_LIMIT_STORE = store
        settings.RATE_LIMIT_CLOCK = clock
        settings.RATE_LIMIT_SUBMISSION_MAX = 2
        settings.RATE_LIMIT_SUBMISSION_WINDOW = 3600

        factory = RequestFactory()

        def dummy_view(request: Any) -> HttpResponse:
            return HttpResponse("ok")

        middleware = SourceScopedRateLimitMiddleware(dummy_view)

        # First 2 requests should pass
        for _ in range(2):
            request = factory.post("/api/v1/submission-drafts")
            request.META["REMOTE_ADDR"] = "10.0.0.1"
            response = middleware(request)
            assert response.status_code == 200

        # Third request should be rate-limited
        request = factory.post("/api/v1/submission-drafts")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        response = middleware(request)
        assert response.status_code == 429
        assert int(response["Retry-After"]) > 0
        assert response["Content-Type"] == "application/problem+json"

    def test_middleware_does_not_affect_other_sources(self, settings: Any) -> None:
        """One rate-limited source does not block another."""
        from django.http import HttpResponse
        from django.test import RequestFactory

        from token_budget_platform.adapters.clock import FrozenClock
        from token_budget_platform.adapters.rate_limiting import InMemoryRateLimitStore
        from token_budget_platform.transports.middleware.rate_limit import (
            SourceScopedRateLimitMiddleware,
        )

        store = InMemoryRateLimitStore()
        clock = FrozenClock(datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC))

        settings.RATE_LIMIT_STORE = store
        settings.RATE_LIMIT_CLOCK = clock
        settings.RATE_LIMIT_SUBMISSION_MAX = 1
        settings.RATE_LIMIT_SUBMISSION_WINDOW = 3600

        factory = RequestFactory()

        def dummy_view(request: Any) -> HttpResponse:
            return HttpResponse("ok")

        middleware = SourceScopedRateLimitMiddleware(dummy_view)

        # Exhaust source A
        request = factory.post("/api/v1/submission-drafts")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        middleware(request)

        request = factory.post("/api/v1/submission-drafts")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        response = middleware(request)
        assert response.status_code == 429

        # Source B is unaffected
        request = factory.post("/api/v1/submission-drafts")
        request.META["REMOTE_ADDR"] = "10.0.0.2"
        response = middleware(request)
        assert response.status_code == 200

    def test_x_forwarded_for_used_as_source(self, settings: Any) -> None:
        """The middleware uses X-Forwarded-For when present (ALB/CloudFront)."""
        from django.test import RequestFactory

        from token_budget_platform.transports.middleware.rate_limit import (
            _extract_network_source,
        )

        factory = RequestFactory()
        request = factory.post("/api/v1/submission-drafts")
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.50, 10.0.0.1"
        request.META["REMOTE_ADDR"] = "10.0.0.1"

        source = _extract_network_source(request)
        assert source == "203.0.113.50"
