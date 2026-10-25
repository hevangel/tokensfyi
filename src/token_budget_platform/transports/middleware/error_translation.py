"""Safe error translation middleware.

Converts unhandled exceptions into application/problem+json responses without
leaking internal details (stack traces, SQL, configuration, etc.).

Requirements:
- 16.5: Record timestamp, correlation ID, deployment version, operation, and
  sanitized error category for unhandled errors.
- 16.11: Return a safe error response containing a correlation identifier and
  a retryable-or-final classification.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from token_budget_platform.domain.telemetry import (
    ErrorClassification,
    OperationalError,
    get_correlation_id,
    sanitize_log_record,
)
from token_budget_platform.transports.api.problems import (
    internal_error,
    service_unavailable,
)

logger = logging.getLogger("token_budget_platform.errors")


def _get_deployment_version() -> str:
    """Retrieve the deployment version from settings."""
    return getattr(settings, "DEPLOYMENT_VERSION", "local-dev")


class SafeErrorTranslationMiddleware:
    """Catches unhandled exceptions and returns safe problem+json responses.

    For API requests (path starts with /api/), returns structured problem+json.
    For HTML requests, re-raises to let Django's standard error handling (or
    custom error templates) take over.

    Logs a sanitized operational error record per Req 16.5.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        try:
            return self.get_response(request)
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _handle_exception(self, request: HttpRequest, exc: Exception) -> HttpResponse:
        correlation_id = get_correlation_id()
        category = ErrorClassification.classify(exc)
        deployment_version = _get_deployment_version()
        operation = f"{request.method} {request.path}"

        # Build and log sanitized operational error (Req 16.5)
        op_error = OperationalError(
            timestamp=datetime.now(UTC),
            correlation_id=correlation_id,
            deployment_version=deployment_version,
            operation=operation,
            error_category=category,
        )
        log_data = sanitize_log_record(op_error.to_log_dict())
        logger.error(
            "Unhandled error: %(error_category)s in %(operation)s",
            log_data,
            exc_info=False,  # Never log exception details to operational output
        )

        # For non-API requests, re-raise to let Django handle (error templates)
        if not request.path.startswith("/api/"):
            raise

        # Return safe classified response (Req 16.11)
        is_retryable = ErrorClassification.is_retryable(category)
        if is_retryable:
            return service_unavailable(correlation_id=correlation_id)
        return internal_error(correlation_id=correlation_id)
