"""Contributor management application service (Req 7.5, 7.6, 8.2-8.7).

Capabilities granted at confirmation allow the contributor to:
- view submission, verification/publication state, and moderation feedback;
- append amendment versions and re-enter review (history preserved);
- withdraw the record, removed from every public repository within 24 hours
  (immediately in this implementation — the deadline is the maximum);
- request recovery links bound to the previously contributed email with
  non-enumerating responses.

All session authorization flows through the scoped management session issued
by the capability exchange; this service assumes the session is already
validated and receives the record id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    ContributorContact,
    ManagementCapability,
    ManagementSession,
    ModerationCase,
    ModerationDecision,
    PublicationState,
    RecordVersion,
    TokenBudgetRecord,
)
from token_budget_platform.application.audit import AuditEntry, audit_on_commit
from token_budget_platform.application.jobs import TransactionalOutbox
from token_budget_platform.application.submission.capability_service import CapabilityService
from token_budget_platform.application.submission.confirmation import (
    _capability_repository,
    _default_key_provider,
)
from token_budget_platform.domain.ports import Clock
from token_budget_platform.domain.submission.management import (
    compute_changed_fields,
    is_material_value_change,
    validate_amendment,
    validate_withdrawal,
)

WITHDRAWAL_REMOVAL_DEADLINE_HOURS = 24
RECOVERY_LINK_MINUTES = 60
DRAFT_EXPIRY_DAYS = 30


class ManagementError(Exception):
    """Base error surfaced as a safe message to the management session."""


class RecordNotFoundError(ManagementError):
    pass


@dataclass(frozen=True, slots=True)
class WithdrawalOutcome:
    record_id: UUID
    withdrawn_at: datetime
    removal_deadline: datetime


@dataclass(frozen=True, slots=True)
class AmendmentOutcome:
    record_id: UUID
    new_version: int
    publication_state: str
    changed_fields: list[str]


class ContributorManagementService:
    """Operations available inside a validated management session."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        outbox: TransactionalOutbox | None = None,
    ) -> None:
        self._clock = clock
        self._outbox = outbox or TransactionalOutbox()

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else timezone.now()

    # ── Status view (Req 8.2, 7.5) ────────────────────────────────────────────

    def status(self, *, record_id: UUID) -> dict[str, Any]:
        """Everything the owning contributor may see about the submission."""
        record = (
            TokenBudgetRecord.objects.filter(id=record_id).select_related("organization").first()
        )
        if record is None:
            raise RecordNotFoundError("Submission not found.")

        decisions = (
            ModerationDecision.objects.filter(case__record_id=record_id)
            .select_related("case")
            .order_by("decided_at")
        )
        feedback = [
            {
                "action": decision.action,
                "reason_code": decision.reason_code,
                "decided_at": decision.decided_at.isoformat(),
            }
            for decision in decisions
        ]

        return {
            "record_id": str(record.id),
            "public_reference": str(record.public_reference),
            "publication_state": record.publication_state,
            "verification_status": record.verification_status,
            "is_withdrawn": record.withdrawn_at is not None,
            "withdrawn_at": record.withdrawn_at.isoformat() if record.withdrawn_at else None,
            "published_at": record.published_at.isoformat() if record.published_at else None,
            "last_reviewed_at": (
                record.last_reviewed_at.isoformat() if record.last_reviewed_at else None
            ),
            "correction_date": (
                record.correction_date.isoformat() if record.correction_date else None
            ),
            "current_version": record.active_version,
            "moderation_feedback": feedback,
            "submission_data": {
                "organization_name": record.organization.name,
                "ai_product": record.ai_product.label,
                "model_provider": record.model_provider.label,
                "workload_category": record.workload_category.label,
                "budget_scope": record.budget_scope,
                "token_budget": str(record.token_budget),
                "reporting_start": record.reporting_start.isoformat(),
                "reporting_end": record.reporting_end.isoformat(),
            },
            "is_public": record.publication_state == PublicationState.APPROVED
            and record.withdrawn_at is None
            and record.suppressed_at is None,
        }

    # ── Amendment (Req 8.4, 12.4, 12.5) ───────────────────────────────────────

    @transaction.atomic
    def amend(
        self,
        *,
        record_id: UUID,
        changes: dict[str, Any],
        expected_version: int | None = None,
        correlation_id: UUID | None = None,
    ) -> AmendmentOutcome:
        """Append a new immutable version and re-enter moderation review.

        History is preserved: prior versions and cases are never rewritten.
        The record returns to QUEUED and a fresh moderation case opens.
        """
        record = (
            TokenBudgetRecord.objects.select_for_update()
            .select_related("organization")
            .filter(id=record_id)
            .first()
        )
        if record is None:
            raise RecordNotFoundError("Submission not found.")

        error = validate_amendment(
            record.publication_state, record.active_version, expected_version, changes
        )
        if error is not None:
            raise ManagementError(error.value)

        unknown = set(changes) - _AMENDABLE_MODEL_FIELDS.keys()
        if unknown:
            raise ManagementError("One or more fields cannot be amended.")

        current_data = {
            field_name: getattr(record, model_field)
            for field_name, model_field in _AMENDABLE_MODEL_FIELDS.items()
        }
        changed_fields = sorted(compute_changed_fields(current_data, changes))
        if not changed_fields:
            raise ManagementError("No changes were supplied.")

        now = self._now()
        new_version = record.active_version + 1

        for field_name, value in changes.items():
            setattr(record, _AMENDABLE_MODEL_FIELDS[field_name], _coerce(field_name, value))

        if is_material_value_change(changed_fields):
            record.correction_date = now.date()

        record.active_version = new_version
        record.publication_state = PublicationState.QUEUED
        record.row_version += 1
        record.save()

        RecordVersion.objects.create(
            record=record,
            version=new_version,
            public_payload={"changed_fields": changed_fields},
            private_payload={},
            changed_fields=changed_fields,
            change_reason="Contributor amendment",
            actor_type="CONTRIBUTOR",
        )

        ModerationCase.objects.create(
            record=record,
            source="SUBMISSION",
            category="AMENDMENT_REVIEW",
            state="OPEN",
            priority=40,
            opened_at=now,
        )
        self._outbox.enqueue(
            aggregate_type="TokenBudgetRecord",
            aggregate_id=record.id,
            event_type="RECALCULATE_METRICS",
            payload={"record_id": str(record.id)},
        )
        audit_on_commit(
            AuditEntry(
                actor_surrogate=None,
                actor_role=None,
                action="SUBMISSION_AMENDED",
                target_type="TokenBudgetRecord",
                target_id=record.id,
                outcome="SUCCESS",
                correlation_id=correlation_id or uuid4(),
                sanitized_metadata={
                    "new_version": new_version,
                    "changed_field_count": len(changed_fields),
                },
            )
        )
        return AmendmentOutcome(
            record_id=record.id,
            new_version=new_version,
            publication_state=record.publication_state,
            changed_fields=changed_fields,
        )

    # ── Withdrawal (Req 8.5, 8.6) ─────────────────────────────────────────────

    @transaction.atomic
    def withdraw(
        self,
        *,
        record_id: UUID,
        correlation_id: UUID | None = None,
    ) -> WithdrawalOutcome:
        """Withdraw the record and remove it from public display immediately.

        The 24-hour deadline is the contractual maximum; the implementation
        removes eligibility in the same transaction so no public query can
        observe the record afterwards.
        """
        record = TokenBudgetRecord.objects.select_for_update().filter(id=record_id).first()
        if record is None:
            raise RecordNotFoundError("Submission not found.")

        error = validate_withdrawal(record.publication_state)
        if error is not None:
            raise ManagementError(error.value)

        now = self._now()
        record.publication_state = PublicationState.WITHDRAWN
        record.withdrawn_at = now
        record.save(
            update_fields=["publication_state", "withdrawn_at", "row_version", "updated_at"]
        )

        # Revoke every management capability: the record is closed (Req 8.6).
        ManagementCapability.objects.filter(record_id=record.id, revoked_at__isnull=True).update(
            revoked_at=now
        )
        ManagementSession.objects.filter(record_id=record.id, revoked_at__isnull=True).update(
            revoked_at=now
        )

        audit_on_commit(
            AuditEntry(
                actor_surrogate=None,
                actor_role=None,
                action="SUBMISSION_WITHDRAWN",
                target_type="TokenBudgetRecord",
                target_id=record.id,
                outcome="SUCCESS",
                correlation_id=correlation_id or uuid4(),
                sanitized_metadata={},
            )
        )
        return WithdrawalOutcome(
            record_id=record.id,
            withdrawn_at=now,
            removal_deadline=now + timedelta(hours=WITHDRAWAL_REMOVAL_DEADLINE_HOURS),
        )

    # ── Recovery (Req 8.7) ────────────────────────────────────────────────────

    def request_recovery(self, *, submitted_email: str, public_reference: UUID | None) -> bool:
        """Send a recovery link only to the previously bound email address.

        Always returns True to the caller.  When the email does not match the
        bound contact for the referenced record (or no record is referenced),
        nothing is sent — the response is non-enumerating.
        """
        from token_budget_platform.adapters.notification import LocalNotificationAdapter
        from token_budget_platform.application.submission.contact_cipher import (
            decrypt_email,
            email_blind_index,
        )
        from token_budget_platform.domain.ports import Notification

        if not submitted_email or public_reference is None:
            return True
        record = TokenBudgetRecord.objects.filter(public_reference=public_reference).first()
        if record is None or record.withdrawn_at is not None:
            return True
        contact = ContributorContact.objects.filter(record=record).first()
        if contact is None or contact.erased_at is not None:
            return True
        if contact.email_blind_index != email_blind_index(submitted_email):
            return True

        bound_address = decrypt_email(contact.encrypted_email)
        issued = self._issue_recovery_capability(record.id)
        sender = LocalNotificationAdapter()
        sender.send(
            Notification(
                channel="EMAIL",
                destination=bound_address,
                template="management_recovery",
                reference_id=record.id,
                parameters={
                    "manage_path": f"/submit/manage/{issued.raw_token}",
                    "expires_minutes": str(RECOVERY_LINK_MINUTES),
                },
            )
        )
        return True

    def _issue_recovery_capability(self, record_id: UUID):
        service = CapabilityService(
            clock=self._clock or _NowClock(self._now),
            key_provider=_default_key_provider(),
            repository=_capability_repository(),
            capability_lifetime=timedelta(minutes=RECOVERY_LINK_MINUTES),
        )
        return service.issue_capability(record_id)


class _NowClock:
    """Clock adapter over a bound ``now`` callable."""

    def __init__(self, now_callable) -> None:
        self._now_callable = now_callable

    def now(self) -> datetime:
        return self._now_callable()


_AMENDABLE_MODEL_FIELDS = {
    "token_budget": "token_budget",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "token_usage": "token_usage",
    "cost_minor_units": "cost_minor_units",
    "cost_currency": "cost_currency",
    "cost_basis": "cost_basis",
    "seat_count": "seat_count",
    "workload_count": "workload_count",
    "reporting_start": "reporting_start",
    "reporting_end": "reporting_end",
    "reporting_timezone": "reporting_timezone",
    "source_date": "source_date",
    "methodology_notes": "methodology_notes",
}

_DATE_FIELDS = {"reporting_start", "reporting_end", "source_date"}
_DECIMAL_FIELDS = {
    "token_budget",
    "input_tokens",
    "output_tokens",
    "token_usage",
    "cost_minor_units",
}


def _coerce(field_name: str, value: Any) -> Any:
    from datetime import date as _date
    from decimal import Decimal as _Decimal

    if value is None:
        return None
    if field_name in _DATE_FIELDS and isinstance(value, str):
        return _date.fromisoformat(value)
    if field_name in _DECIMAL_FIELDS:
        return value if isinstance(value, _Decimal) else _Decimal(str(value))
    return value
