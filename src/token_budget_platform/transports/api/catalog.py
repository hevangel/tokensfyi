"""Versioned public API for the catalog, submissions, and reports.

All responses carry the problem+json error shape from ``problems.py`` and
exclusively the public DTO projection — no private model data can reach a
response (Req 1.3, 9.3, 16.11).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from token_budget_platform.application.catalog.search import (
    ComparisonError,
    comparison,
    parse_comparison_references,
    parse_filters,
    record_detail,
    search_records,
)
from token_budget_platform.domain.telemetry import get_correlation_id
from token_budget_platform.transports.api.problems import (
    PROBLEM_TYPE_NOT_FOUND,
    PROBLEM_TYPE_VALIDATION,
    problem_response,
)


def _catalog_payload(page) -> dict[str, Any]:
    return {
        "total_count": page.total_count,
        "page": page.page_number,
        "total_pages": page.total_pages,
        "headings": page.headings,
        "records": [record.to_dict() for record in page.records],
        "empty_dataset": page.is_empty_dataset,
        "scope_explanation": page.scope_explanation,
    }


@require_GET
def api_browse(request: HttpRequest) -> JsonResponse:
    """Search and paginate publicly eligible records."""
    filters, errors = parse_filters(request.GET)
    try:
        page_number = max(1, int(str(request.GET.get("page", "1") or "1")))
    except ValueError:
        page_number = 1
        errors = errors + ["Invalid page number."]
    result = search_records(filters, page=page_number)
    body = _catalog_payload(result)
    body["filter_errors"] = errors
    body["query"] = filters.active()
    return JsonResponse(body)


@require_GET
def api_record(request: HttpRequest, record_id) -> JsonResponse:
    """One publicly eligible record by public reference."""
    try:
        reference = record_id if isinstance(record_id, UUID) else UUID(str(record_id))
    except (ValueError, AttributeError):
        return problem_response(
            status=404,
            title="Not Found",
            problem_type=PROBLEM_TYPE_NOT_FOUND,
            detail="No publicly available record has that reference.",
            correlation_id=get_correlation_id(),
        )
    detail = record_detail(reference)
    if detail is None:
        return problem_response(
            status=404,
            title="Not Found",
            problem_type=PROBLEM_TYPE_NOT_FOUND,
            detail="No publicly available record has that reference.",
            correlation_id=get_correlation_id(),
        )
    return JsonResponse(
        {
            "record": detail.record.to_dict(),
            "disputed": detail.disputed,
            "correction_date": detail.correction_date.isoformat()
            if detail.correction_date
            else None,
            "metrics": detail.metrics,
        }
    )


@require_GET
def api_compare(request: HttpRequest) -> JsonResponse:
    """Aligned comparison of two through five records."""
    raw_ids = str(request.GET.get("ids", ""))
    try:
        references = parse_comparison_references(raw_ids)
        result = comparison(references)
    except ComparisonError as exc:
        return problem_response(
            status=400,
            title="Bad Request",
            problem_type=PROBLEM_TYPE_VALIDATION,
            detail=str(exc),
            correlation_id=get_correlation_id(),
        )
    return JsonResponse(
        {
            "columns": [column.to_dict() for column in result.columns],
            "rows": result.rows,
            "metric_rows": result.metric_rows,
        }
    )


@csrf_exempt
@require_POST
def api_submit(request: HttpRequest) -> JsonResponse:
    """Confirm a submission payload atomically (idempotency-key honored)."""
    from token_budget_platform.application.submission.confirmation import (
        ConfirmationError,
        SubmissionConfirmationService,
        preview_confirmation,
    )
    from token_budget_platform.application.submission.service import SubmissionService

    correlation_id = get_correlation_id()
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, ValueError):
        return problem_response(
            status=400,
            title="Bad Request",
            problem_type=PROBLEM_TYPE_VALIDATION,
            detail="Request body must be valid JSON.",
            correlation_id=correlation_id,
        )
    if not isinstance(payload, dict):
        return problem_response(
            status=400,
            title="Bad Request",
            problem_type=PROBLEM_TYPE_VALIDATION,
            detail="Request body must be a JSON object.",
            correlation_id=correlation_id,
        )

    # Validate first; valid sibling preservation comes from the shared validator.
    validation = SubmissionService().validate_full(payload)
    if not validation.valid:
        return JsonResponse(
            {
                "type": PROBLEM_TYPE_VALIDATION,
                "title": "Validation failed",
                "status": 400,
                "errors": [
                    {"field": error.field, "code": error.code, "message": error.message}
                    for error in validation.errors
                ],
            },
            status=400,
        )

    idempotency_key = request.headers.get("Idempotency-Key", "")
    preview = request.headers.get("Prefer", "") == "return=preview"
    if preview:
        return JsonResponse({"preview": preview_confirmation(payload)})

    service = SubmissionConfirmationService()
    try:
        result = service.confirm_payload(
            payload=payload,
            requester_scope="submission-api",
            idempotency_key=idempotency_key,
            correlation_id=_as_uuid(correlation_id),
        )
    except ConfirmationError as exc:
        return problem_response(
            status=400,
            title="Bad Request",
            problem_type=PROBLEM_TYPE_VALIDATION,
            detail=str(exc),
            correlation_id=correlation_id,
        )

    body = {
        "record_id": str(result.record_id),
        "public_reference": str(result.public_reference),
        "organization_name": result.organization_name,
        "ai_product": result.ai_product,
        "token_budget": result.token_budget,
        "reporting_start": result.reporting_start.isoformat(),
        "reporting_end": result.reporting_end.isoformat(),
        "verification_status": result.verification_status,
        "management_token": result.management_token,
        "replayed": result.replayed,
    }
    return JsonResponse(body, status=201)


@csrf_exempt
@require_POST
def api_report(request: HttpRequest) -> JsonResponse:
    """File a community report; sensitive categories suppress synchronously."""
    from token_budget_platform.application.moderation.service import (
        ModerationError,
        ModerationService,
    )

    correlation_id = get_correlation_id()
    try:
        body = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, ValueError):
        return problem_response(
            status=400,
            title="Bad Request",
            problem_type=PROBLEM_TYPE_VALIDATION,
            detail="Request body must be valid JSON.",
            correlation_id=correlation_id,
        )
    if not isinstance(body, dict):
        return problem_response(
            status=400,
            title="Bad Request",
            problem_type=PROBLEM_TYPE_VALIDATION,
            detail="Request body must be a JSON object.",
            correlation_id=correlation_id,
        )

    reference = str(body.get("record_reference", ""))
    try:
        outcome = ModerationService().file_report(
            record_reference=UUID(reference),
            category=str(body.get("category", "")),
            explanation=str(body.get("explanation", "")),
            reporter_pseudonym=str(body.get("reporter_pseudonym", "") or ""),
            correlation_id=_as_uuid(correlation_id),
        )
    except (ModerationError, ValueError) as exc:
        return problem_response(
            status=400,
            title="Bad Request",
            problem_type=PROBLEM_TYPE_VALIDATION,
            detail=str(exc),
            correlation_id=correlation_id,
        )

    return JsonResponse(
        {
            "case_id": str(outcome.case_id),
            "record_reference": str(outcome.record_reference),
            "suppressed": outcome.suppressed,
            "disputed": outcome.disputed,
        },
        status=201,
    )


def _as_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
