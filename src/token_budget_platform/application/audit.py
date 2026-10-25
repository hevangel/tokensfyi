"""Append-only audit recording with independent failure handling.

Requirements: 7.10, 7.11, 7.12, 9.8, 10.11
- A privileged transaction attempts its audit append exactly once, after the
  domain transaction commits.
- An audit failure never rolls back or blocks the committed action; it emits
  sanitized high-severity operational metadata only.
- Audit rows are append-only: callers have no update/delete path here, and
  failure handling preserves existing rows untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from token_budget_platform.adapters.django.models import AuditRecord
from token_budget_platform.domain.telemetry import sanitize_value


def _sanitize_audit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Scrub sensitive patterns from audit metadata string values."""

    def _clean(value: Any) -> Any:
        if isinstance(value, str):
            return sanitize_value(value)
        if isinstance(value, dict):
            return {key: _clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_clean(item) for item in value]
        return value

    return {key: _clean(value) for key, value in metadata.items()}


logger = logging.getLogger("token_budget_platform.audit")


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One privileged-action audit attempt."""

    actor_surrogate: UUID | None
    actor_role: str | None
    action: str
    target_type: str
    target_id: UUID | None
    outcome: str
    correlation_id: UUID
    sanitized_metadata: dict[str, Any]
    source_network_pseudonym: str = ""
    occurred_at: datetime | None = None


def record_audit(entry: AuditEntry) -> bool:
    """Append one audit row after a privileged action, exactly once.

    Returns True when the row is stored; False when the append failed.
    Never raises: an audit failure must not roll back or surface as an error
    for the already-committed privileged action (Req 7.11).
    """
    try:
        with transaction.atomic():
            last = AuditRecord.objects.order_by("-sequence").first()
            sequence = (last.sequence + 1) if last is not None else 1
            AuditRecord.objects.create(
                sequence=sequence,
                actor_surrogate=entry.actor_surrogate,
                actor_role=entry.actor_role,
                action=entry.action[:100],
                target_type=entry.target_type[:100],
                target_id=entry.target_id,
                occurred_at=entry.occurred_at or timezone.now(),
                outcome=entry.outcome[:32],
                correlation_id=entry.correlation_id,
                source_network_pseudonym=entry.source_network_pseudonym[:128],
                sanitized_metadata=_sanitize_audit_metadata(entry.sanitized_metadata),
            )
        return True
    except Exception:
        _emit_audit_failure(entry)
        return False


def audit_on_commit(entry: AuditEntry) -> None:
    """Register the audit append to run only after the current transaction commits.

    If the transaction rolls back, no audit row is written — the privileged
    action never happened.  If the append itself fails afterwards, the
    sanitized failure signal is emitted without re-raising.
    """
    transaction.on_commit(lambda: record_audit(entry))


def _emit_audit_failure(entry: AuditEntry) -> None:
    """Emit sanitized high-severity operational metadata about the failure.

    The payload contains category and correlation identifiers only — never
    actor identity, target content, credentials, or raw private values.
    """
    logger.error(
        "audit_append_failed",
        extra={
            "category": "AUDIT_WRITE_FAILURE",
            "severity": "HIGH",
            "correlation_id": str(entry.correlation_id),
            "action_category": entry.action[:100],
            "target_type": entry.target_type[:100],
            "attempted_at": timezone.now().isoformat(),
        },
    )


def attempt_audit_integrity_intact() -> bool:
    """Cheap integrity check for operations: audit storage is reachable."""
    try:
        AuditRecord.objects.exists()
        return True
    except IntegrityError:
        return False
