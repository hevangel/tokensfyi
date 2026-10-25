"""Internal backup event ingestion endpoint.

This endpoint receives signed backup status events and updates the production
mutation gate accordingly. It is intended for internal use by the backup
monitoring infrastructure (AWS Backup / EventBridge / operator scripts).

Requirements:
- 16.7: Required_Backups on a schedule that supports 24-hour RPO.
- 16.8: Backup failure suspends production mutations until success.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from token_budget_platform.adapters.aws.backup import InvalidBackupEvent, SignedBackupGate
from token_budget_platform.domain.telemetry import get_correlation_id
from token_budget_platform.transports.api.problems import (
    PROBLEM_TYPE_FORBIDDEN,
    PROBLEM_TYPE_VALIDATION,
    problem_response,
)

logger = logging.getLogger("token_budget_platform.backup_events")


@csrf_exempt
@require_POST
def ingest_backup_event(request: HttpRequest) -> JsonResponse:
    """Accept a signed backup state event and update the mutation gate.

    The request must include:
    - Body: JSON object with at minimum "status" ("SUCCEEDED" or "FAILED")
    - Header X-Backup-Signature: HMAC-SHA256 hex digest of the canonical body

    The endpoint is exempt from CSRF (it's machine-to-machine) and from the
    backup gate middleware itself (so the gate can be reopened).
    """
    correlation_id = get_correlation_id()

    # Resolve the backup gate
    gate = _get_ingestable_gate()
    if gate is None:
        logger.info(
            "backup_event_ignored_no_gate",
            extra={"correlation_id": correlation_id},
        )
        return JsonResponse(
            {"status": "ignored", "detail": "No backup gate configured."},
            status=200,
        )

    # Parse request body
    try:
        body = json.loads(request.body)
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

    # Extract signature from header
    signature = request.META.get("HTTP_X_BACKUP_SIGNATURE", "")
    if not signature:
        logger.warning(
            "backup_event_missing_signature",
            extra={"correlation_id": correlation_id},
        )
        return problem_response(
            status=403,
            title="Forbidden",
            problem_type=PROBLEM_TYPE_FORBIDDEN,
            detail="Missing X-Backup-Signature header.",
            correlation_id=correlation_id,
        )

    # Ingest the event through the gate adapter
    try:
        decision = gate.ingest(body, signature)
    except InvalidBackupEvent as exc:
        logger.warning(
            "backup_event_rejected",
            extra={
                "correlation_id": correlation_id,
                "error": str(exc),
            },
        )
        return problem_response(
            status=403,
            title="Forbidden",
            problem_type=PROBLEM_TYPE_FORBIDDEN,
            detail="Invalid backup event.",
            correlation_id=correlation_id,
        )

    # Persist the gate state to the OperationalGate model for recovery
    _persist_gate_state(decision)

    return JsonResponse(
        {
            "status": "accepted",
            "gate_allowed": decision.allowed,
            "reason_code": decision.reason_code,
        },
        status=200,
    )


def _get_ingestable_gate() -> SignedBackupGate | None:
    """Resolve a gate adapter that supports the ingest method.

    Returns None if no gate is configured or it does not support ingestion.
    """
    gate = getattr(settings, "BACKUP_GATE", None)
    if gate is None:
        return None
    if not isinstance(gate, SignedBackupGate):
        return None
    return gate


def _persist_gate_state(decision) -> None:
    """Persist the gate state to the database for recovery across restarts.

    Uses the OperationalGate model to track the last known gate state.
    This allows the application to restore the gate state after a restart.
    """
    try:
        from token_budget_platform.adapters.django.models import OperationalGate

        environment = getattr(settings, "ENVIRONMENT_NAME", "production")
        OperationalGate.objects.update_or_create(
            environment=environment,
            gate_type="BACKUP_MUTATIONS",
            defaults={
                "is_open": decision.allowed,
                "signed_source_event_reference": decision.source_reference or "",
                "reason_code": decision.reason_code,
                "observed_at": decision.observed_at,
            },
        )
    except Exception:
        # Persistence failure must not prevent the in-memory gate from updating.
        # The gate remains correct in memory; persistence is for restart recovery.
        logger.exception("backup_gate_persist_failed")
