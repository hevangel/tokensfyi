"""RFC 9457 Problem Details responses for the versioned API.

All public error responses use application/problem+json with a correlation ID,
a safe classified type, and no internal details leaked to external consumers.
"""

from __future__ import annotations

from typing import Any

from django.http import JsonResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Safe problem type URIs (relative references per RFC 9457 §4.2.1)
PROBLEM_TYPE_VALIDATION = "/problems/validation-error"
PROBLEM_TYPE_NOT_FOUND = "/problems/not-found"
PROBLEM_TYPE_CONFLICT = "/problems/conflict"
PROBLEM_TYPE_PRECONDITION_FAILED = "/problems/precondition-failed"
PROBLEM_TYPE_IDEMPOTENCY_CONFLICT = "/problems/idempotency-conflict"
PROBLEM_TYPE_RATE_LIMITED = "/problems/rate-limited"
PROBLEM_TYPE_FORBIDDEN = "/problems/forbidden"
PROBLEM_TYPE_UNAUTHORIZED = "/problems/unauthorized"
PROBLEM_TYPE_INTERNAL = "/problems/internal-error"
PROBLEM_TYPE_SERVICE_UNAVAILABLE = "/problems/service-unavailable"
PROBLEM_TYPE_BODY_TOO_LARGE = "/problems/body-too-large"
PROBLEM_TYPE_UNSUPPORTED_MEDIA = "/problems/unsupported-media-type"


def problem_response(
    *,
    status: int,
    title: str,
    problem_type: str = "about:blank",
    detail: str | None = None,
    instance: str | None = None,
    correlation_id: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JsonResponse:
    """Build a typed problem+json response following RFC 9457."""
    body: dict[str, Any] = {
        "type": problem_type,
        "title": title,
        "status": status,
    }
    if detail is not None:
        body["detail"] = detail
    if instance is not None:
        body["instance"] = instance
    if correlation_id is not None:
        body["correlation_id"] = correlation_id
    if extra:
        body.update(extra)

    response = JsonResponse(body, status=status, content_type=PROBLEM_CONTENT_TYPE)
    if headers:
        for key, value in headers.items():
            response[key] = value
    return response


def validation_error(
    *,
    detail: str,
    correlation_id: str | None = None,
    errors: list[dict[str, str]] | None = None,
) -> JsonResponse:
    """400 response for request validation failures."""
    extra = {"errors": errors} if errors else None
    return problem_response(
        status=400,
        title="Validation Error",
        problem_type=PROBLEM_TYPE_VALIDATION,
        detail=detail,
        correlation_id=correlation_id,
        extra=extra,
    )


def not_found(*, correlation_id: str | None = None) -> JsonResponse:
    """404 response with no information leakage."""
    return problem_response(
        status=404,
        title="Not Found",
        problem_type=PROBLEM_TYPE_NOT_FOUND,
        detail="The requested resource does not exist.",
        correlation_id=correlation_id,
    )


def conflict(
    *,
    detail: str = "The resource was modified by another request.",
    correlation_id: str | None = None,
) -> JsonResponse:
    """409 response for optimistic concurrency violations."""
    return problem_response(
        status=409,
        title="Conflict",
        problem_type=PROBLEM_TYPE_CONFLICT,
        detail=detail,
        correlation_id=correlation_id,
    )


def precondition_failed(*, correlation_id: str | None = None) -> JsonResponse:
    """412 response for If-Match ETag mismatches."""
    return problem_response(
        status=412,
        title="Precondition Failed",
        problem_type=PROBLEM_TYPE_PRECONDITION_FAILED,
        detail="The If-Match header does not match the current resource version.",
        correlation_id=correlation_id,
    )


def idempotency_conflict(*, correlation_id: str | None = None) -> JsonResponse:
    """409 response when an Idempotency-Key replays with a different request body."""
    return problem_response(
        status=409,
        title="Idempotency Conflict",
        problem_type=PROBLEM_TYPE_IDEMPOTENCY_CONFLICT,
        detail="The Idempotency-Key was used with a different request payload.",
        correlation_id=correlation_id,
    )


def rate_limited(
    *,
    retry_after: int,
    correlation_id: str | None = None,
) -> JsonResponse:
    """429 response with a positive Retry-After value."""
    return problem_response(
        status=429,
        title="Too Many Requests",
        problem_type=PROBLEM_TYPE_RATE_LIMITED,
        detail="Rate limit exceeded. Retry after the indicated interval.",
        correlation_id=correlation_id,
        headers={"Retry-After": str(retry_after)},
    )


def forbidden(*, correlation_id: str | None = None) -> JsonResponse:
    """403 response for authorization failures."""
    return problem_response(
        status=403,
        title="Forbidden",
        problem_type=PROBLEM_TYPE_FORBIDDEN,
        detail="You do not have permission to perform this action.",
        correlation_id=correlation_id,
    )


def unauthorized(*, correlation_id: str | None = None) -> JsonResponse:
    """401 response for missing or invalid credentials."""
    return problem_response(
        status=401,
        title="Unauthorized",
        problem_type=PROBLEM_TYPE_UNAUTHORIZED,
        detail="Authentication is required.",
        correlation_id=correlation_id,
    )


def internal_error(*, correlation_id: str | None = None) -> JsonResponse:
    """500 response with no internal details exposed (Req 16.11)."""
    return problem_response(
        status=500,
        title="Internal Server Error",
        problem_type=PROBLEM_TYPE_INTERNAL,
        detail="An unexpected error occurred. Use the correlation_id for support.",
        correlation_id=correlation_id,
    )


def service_unavailable(*, correlation_id: str | None = None) -> JsonResponse:
    """503 response for unhealthy service state."""
    return problem_response(
        status=503,
        title="Service Unavailable",
        problem_type=PROBLEM_TYPE_SERVICE_UNAVAILABLE,
        detail="The service is temporarily unavailable.",
        correlation_id=correlation_id,
    )


def body_too_large(*, correlation_id: str | None = None) -> JsonResponse:
    """413 response for oversized request bodies."""
    return problem_response(
        status=413,
        title="Payload Too Large",
        problem_type=PROBLEM_TYPE_BODY_TOO_LARGE,
        detail="The request body exceeds the permitted maximum size.",
        correlation_id=correlation_id,
    )
