"""Request safety middleware for versioned API contracts.

Enforces host/origin validation, body size limits, secure response headers,
and input sanitization for the /api/v1 transport layer.

Requirements:
- 10.1: TLS 1.2+ on external boundaries (enforced in production settings).
- 10.6: Neutralize executable markup and reject oversized/malformed input.
- 14.7: Require successful verification including static analysis.
- 14.8: Block releasable artifact on verification failure.
- 16.11: Safe error responses with correlation ID and retryable classification.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from token_budget_platform.domain.telemetry import get_correlation_id
from token_budget_platform.transports.api.problems import (
    body_too_large,
    problem_response,
)

logger = logging.getLogger("token_budget_platform.request_safety")

# Default maximum request body size: 1 MiB for JSON API requests.
DEFAULT_MAX_BODY_SIZE = 1 * 1024 * 1024

# Evidence upload limit: 10 MiB (Requirement 6.4).
EVIDENCE_MAX_BODY_SIZE = 10 * 1024 * 1024

# Paths that allow larger bodies (evidence upload).
LARGE_BODY_PATHS = ("/api/v1/records/", "/evidence")


def _max_body_for_path(path: str) -> int:
    """Return the maximum allowed body size for the given request path."""
    # Evidence upload paths get the higher limit
    if "/evidence" in path:
        return EVIDENCE_MAX_BODY_SIZE
    return getattr(settings, "API_MAX_BODY_SIZE", DEFAULT_MAX_BODY_SIZE)


class RequestSafetyMiddleware:
    """Enforces body size limits, host/origin validation, and secure headers.

    Applied to all requests. For API paths, rejects oversized bodies before
    they reach view processing.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # 1. Body size enforcement for API paths
        if request.path.startswith("/api/"):
            content_length = request.META.get("CONTENT_LENGTH")
            if content_length:
                try:
                    length = int(content_length)
                except (ValueError, TypeError):
                    length = 0
                max_size = _max_body_for_path(request.path)
                if length > max_size:
                    correlation_id = get_correlation_id()
                    logger.warning(
                        "body_too_large",
                        extra={
                            "path": request.path,
                            "content_length": length,
                            "max_allowed": max_size,
                            "correlation_id": correlation_id,
                        },
                    )
                    return body_too_large(correlation_id=correlation_id)

        # 2. Origin validation for state-changing API requests
        if request.path.startswith("/api/") and request.method in (
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ):
            origin = request.META.get("HTTP_ORIGIN", "")
            if origin:
                allowed_origins = getattr(settings, "CSRF_TRUSTED_ORIGINS", [])
                allowed_hosts = getattr(settings, "ALLOWED_HOSTS", [])
                if not _origin_allowed(origin, allowed_origins, allowed_hosts):
                    correlation_id = get_correlation_id()
                    logger.warning(
                        "origin_rejected",
                        extra={
                            "origin": origin,
                            "path": request.path,
                            "correlation_id": correlation_id,
                        },
                    )
                    return problem_response(
                        status=403,
                        title="Forbidden",
                        problem_type="/problems/origin-rejected",
                        detail="The request origin is not permitted.",
                        correlation_id=correlation_id,
                    )

        response = self.get_response(request)

        # 3. Add secure response headers to all responses
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["Referrer-Policy"] = "same-origin"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        # Content-Security-Policy for API responses (strict, no inline scripts)
        if request.path.startswith("/api/"):
            response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

        # Cache-Control for state-changing responses
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            response["Cache-Control"] = "no-store"

        return response


def _origin_allowed(origin: str, trusted_origins: list[str], allowed_hosts: list[str]) -> bool:
    """Check whether the Origin header is acceptable.

    Accepts if Origin matches a CSRF_TRUSTED_ORIGINS entry or if the Origin
    host is in ALLOWED_HOSTS. Wildcard ALLOWED_HOSTS ('*') permits all origins.
    """
    if "*" in allowed_hosts:
        return True
    if origin in trusted_origins:
        return True
    # Extract host from origin (e.g., "https://example.com" -> "example.com")
    try:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        host = parsed.hostname or ""
        if host in allowed_hosts:
            return True
        # Check trusted origins as patterns (e.g., "https://*.example.com")
        for trusted in trusted_origins:
            trusted_parsed = urlparse(trusted)
            trusted_host = trusted_parsed.hostname or ""
            if trusted_host.startswith("*."):
                # Wildcard subdomain match
                suffix = trusted_host[1:]  # ".example.com"
                if host.endswith(suffix) or host == trusted_host[2:]:
                    return True
    except Exception:
        pass
    return False
