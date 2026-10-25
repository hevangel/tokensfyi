"""Correlation ID middleware for request tracing.

Generates or propagates X-Correlation-ID headers across the request lifecycle.
The correlation ID is stored in a context variable accessible to SQL logging,
outbox/job enqueueing, scanning, notifications, and structured log output.

Requirements: 7.12, 16.5, 16.11
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from token_budget_platform.domain.telemetry import (
    generate_correlation_id,
    set_correlation_id,
)

CORRELATION_HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware:
    """Django middleware that propagates or generates a correlation ID.

    The ID is set in a context variable at the start of the request and
    included in the response headers.  Downstream code (SQL adapters, job
    handlers, notification adapters) can retrieve it via
    ``get_correlation_id()``.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Accept an incoming correlation ID or generate a new one
        incoming = request.META.get("HTTP_X_CORRELATION_ID", "").strip()
        correlation_id = incoming if incoming else generate_correlation_id()

        # Store in context variable for cross-cutting access
        set_correlation_id(correlation_id)

        # Also stash on request for convenience in views
        request.correlation_id = correlation_id  # type: ignore[attr-defined]

        response = self.get_response(request)

        # Echo back in response headers
        response[CORRELATION_HEADER] = correlation_id
        return response
