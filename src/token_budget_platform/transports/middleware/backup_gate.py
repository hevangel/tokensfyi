"""Production mutation gate middleware.

Rejects all non-exempt data-changing requests when the backup gate is closed.
Public reads and health checks continue to function, ensuring the system
remains available for read-only traffic while protecting data integrity.

Requirements:
- 16.8: IF a Required_Backup fails, THEN THE Token_Budget_Platform SHALL suspend
  production data-changing operations until a Required_Backup succeeds, while
  allowing public reads and Health_Check requests to continue.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from token_budget_platform.domain.ports import BackupGatePort
from token_budget_platform.domain.telemetry import get_correlation_id
from token_budget_platform.transports.api.problems import (
    PROBLEM_TYPE_SERVICE_UNAVAILABLE,
    problem_response,
)

logger = logging.getLogger("token_budget_platform.backup_gate")

# HTTP methods that represent data-changing (mutation) operations.
MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths that are always exempt from the backup gate, even for mutations.
# Health checks and the backup event ingestion endpoint itself must remain
# operational so the gate can be reopened.
EXEMPT_PATH_PREFIXES = (
    "/health/",
    "/api/v1/internal/backup-events",
)


def _is_exempt(request: HttpRequest) -> bool:
    """Return True if this request is exempt from the mutation gate.

    Exempt requests:
    - All safe (read-only) HTTP methods (GET, HEAD, OPTIONS)
    - Health check endpoints
    - The backup event ingestion endpoint (so we can reopen the gate)
    """
    if request.method not in MUTATION_METHODS:
        return True
    for prefix in EXEMPT_PATH_PREFIXES:
        if request.path.startswith(prefix):
            return True
    return False


class BackupMutationGateMiddleware:
    """Reject mutations when the production backup gate is closed.

    When the gate is closed (due to a required backup failure), all
    data-changing API requests receive a 503 Service Unavailable response
    with a clear problem type. Public reads continue unaffected.

    The gate adapter is resolved from Django settings at middleware init time.
    In local/test environments, the adapter is always-open by default.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if _is_exempt(request):
            return self.get_response(request)

        gate = _get_backup_gate()
        if gate is None:
            # No gate configured (e.g., local development without explicit setup).
            return self.get_response(request)

        decision = gate.mutation_allowed()
        if decision.allowed:
            return self.get_response(request)

        # Gate is closed - reject the mutation without any state change.
        correlation_id = get_correlation_id()
        logger.warning(
            "mutation_rejected_backup_gate_closed",
            extra={
                "path": request.path,
                "method": request.method,
                "reason_code": decision.reason_code,
                "correlation_id": correlation_id,
            },
        )
        return problem_response(
            status=503,
            title="Service Unavailable",
            problem_type=PROBLEM_TYPE_SERVICE_UNAVAILABLE,
            detail=(
                "Data-changing operations are temporarily suspended pending "
                "successful backup verification. Read operations remain available."
            ),
            correlation_id=correlation_id,
            extra={"retryable": True},
        )


def _get_backup_gate() -> BackupGatePort | None:
    """Resolve the backup gate adapter from Django settings.

    Returns None if no gate is configured (safe for local development
    where mutations should always be allowed).
    """
    return getattr(settings, "BACKUP_GATE", None)
