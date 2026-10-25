"""Moderation application service: queue leases, publication decisions, and reports.

Requirements:
- 7.2/7.3: priority-ordered queue, claim/release leases with expiry,
  optimistic version checks, approve/reject/changes-requested decisions.
- 7.4/7.5: approval publishes; rejection/changes carry contributor-visible
  structured reasons; public eligibility is enforced through the single
  predicate everywhere.
- 7.7/7.8: community reports validate category/explanation, create linked
  cases, and privacy-sensitive categories suppress the record from public
  display synchronously before the response returns; caches are purged.
- 7.9: resolutions record decision, reason, timestamp, and resulting status;
  only state-valid dispute resolutions are accepted.
- 4.6: ordinary dispute reports set Disputed status until resolved.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    ModerationCase,
    ModerationCaseState,
    ModerationDecision,
    ModerationSource,
    PublicationState,
    StaffUser,
    TokenBudgetRecord,
    VerificationStatus,
)
from token_budget_platform.application.audit import AuditEntry, audit_on_commit
from token_budget_platform.application.telemetry_sanitization import encrypt_private_text
from token_budget_platform.domain.moderation.decisions import DecisionAction
from token_budget_platform.domain.moderation.priorities import priority_for_source
from token_budget_platform.domain.moderation.reports import (
    SENSITIVE_REPORT_CATEGORIES,
    ReportCategory,
    validate_report,
)
from token_budget_platform.domain.moderation.state_machine import (
    validate_dispute_resolution_status,
    validate_publication_transition,
)
from token_budget_platform.domain.ports import Clock

DEFAULT_LEASE_MINUTES = 30
MAX_LEASE_MINUTES = 120


class ModerationError(Exception):
    """Base error for moderation operations."""


class CaseNotFoundError(ModerationError):
    pass


class LeaseConflictError(ModerationError):
    """Another moderator holds an unexpired lease on the case."""


class InvalidTransitionError(ModerationError):
    pass


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One queued case rendered for moderators (no private content)."""

    case_id: UUID
    record_id: UUID
    public_reference: UUID
    source: str
    category: str
    state: str
    priority: int
    opened_at: datetime
    lease_expires_at: datetime | None
    organization_name: str
    ai_product: str
    token_budget: str
    verification_status: str
    duplicate_flag: bool


@dataclass(frozen=True, slots=True)
class ClaimResult:
    case_id: UUID
    lease_expires_at: datetime
    record_summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    success: bool
    case_id: UUID | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """Result of filing a community report.

    ``suppressed`` is True when a privacy-sensitive report removed the record
    from public display before this response was produced (Req 7.8).
    """

    case_id: UUID
    record_reference: UUID
    suppressed: bool
    disputed: bool


class ModerationService:
    """Coordinates the moderation queue, decisions, and community reports."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else timezone.now()

    # ── Queue (Req 7.2) ───────────────────────────────────────────────────────

    def queue(self, *, state: str = ModerationCaseState.OPEN, limit: int = 50) -> list[QueueEntry]:
        """Priority-ordered queue entries; no private content is included."""
        rows = (
            ModerationCase.objects.filter(state=state)
            .select_related("record", "record__organization", "record__ai_product")
            .order_by("priority", "opened_at", "id")[:limit]
        )
        return [
            QueueEntry(
                case_id=row.id,
                record_id=row.record_id,
                public_reference=row.record.public_reference,
                source=row.source,
                category=row.category,
                state=row.state,
                priority=row.priority,
                opened_at=row.opened_at,
                lease_expires_at=row.lease_expires_at,
                organization_name=row.record.organization.name,
                ai_product=row.record.ai_product.label,
                token_budget=str(row.record.token_budget),
                verification_status=row.record.verification_status,
                duplicate_flag=row.record.duplicate_flag,
            )
            for row in rows
        ]

    @transaction.atomic
    def claim(
        self, *, case_id: UUID, staff: StaffUser, minutes: int = DEFAULT_LEASE_MINUTES
    ) -> ClaimResult:
        """Claim a lease on an open case with an optimistic version check."""
        lease_seconds = max(1, min(minutes, MAX_LEASE_MINUTES)) * 60
        now = self._now()
        case = (
            ModerationCase.objects.select_for_update()
            .select_related("record", "record__organization", "record__ai_product")
            .filter(id=case_id, state=ModerationCaseState.OPEN)
            .first()
        )
        if case is None:
            raise CaseNotFoundError("The case is not available.")
        if (
            case.lease_expires_at is not None
            and case.lease_expires_at > now
            and case.assigned_staff_id != staff.id
        ):
            raise LeaseConflictError("Another moderator is currently working on this case.")

        case.assigned_staff = staff
        case.lease_expires_at = now + timedelta(seconds=lease_seconds)
        case.row_version += 1
        case.save(update_fields=["assigned_staff", "lease_expires_at", "row_version", "updated_at"])

        return ClaimResult(
            case_id=case.id,
            lease_expires_at=case.lease_expires_at,
            record_summary={
                "public_reference": str(case.record.public_reference),
                "organization_name": case.record.organization.name,
                "ai_product": case.record.ai_product.label,
                "budget_scope": case.record.budget_scope,
                "token_budget": str(case.record.token_budget),
                "reporting_start": case.record.reporting_start.isoformat(),
                "reporting_end": case.record.reporting_end.isoformat(),
                "verification_status": case.record.verification_status,
                "duplicate_flag": case.record.duplicate_flag,
            },
        )

    @transaction.atomic
    def release(self, *, case_id: UUID, staff: StaffUser) -> None:
        """Release a held lease; expired leases of others are untouched."""
        case = ModerationCase.objects.select_for_update().filter(id=case_id).first()
        if case is None or case.assigned_staff_id != staff.id:
            return
        case.assigned_staff = None
        case.lease_expires_at = None
        case.save(update_fields=["assigned_staff", "lease_expires_at", "row_version", "updated_at"])

    # ── Decisions (Req 7.4, 7.5, 7.9) ─────────────────────────────────────────

    @transaction.atomic
    def decide(
        self,
        *,
        case_id: UUID,
        staff: StaffUser,
        action: str,
        reason_code: str,
        rationale: str = "",
        resulting_verification_status: str | None = None,
        target_status_for_dispute: str | None = None,
        correlation_id: UUID | None = None,
    ) -> DecisionOutcome:
        """Apply a publication decision to a case and its record.

        Enforces the publication state machine, records the immutable decision
        row with the structured reason, updates public eligibility through the
        record fields alone, and appends the audit attempt after commit.
        """
        now = self._now()
        case = (
            ModerationCase.objects.select_for_update()
            .select_related("record")
            .filter(id=case_id)
            .first()
        )
        if case is None:
            return DecisionOutcome(success=False, error="case_not_found")
        if case.state != ModerationCaseState.OPEN:
            return DecisionOutcome(success=False, error="case_not_open")

        # Lease and optimistic version enforcement (Req 7.3).
        if case.assigned_staff_id != staff.id:
            return DecisionOutcome(success=False, error="lease_required")
        if case.lease_expires_at is not None and case.lease_expires_at < now:
            return DecisionOutcome(success=False, error="lease_expired")

        record = case.record
        try:
            action_enum = DecisionAction(action)
        except ValueError:
            return DecisionOutcome(success=False, error="unknown_action")

        if action_enum is DecisionAction.APPROVE:
            return self._apply_approval(
                case, record, staff, action_enum, reason_code, rationale, now, correlation_id
            )
        if action_enum is DecisionAction.REJECT:
            return self._apply_rejection(
                case, record, staff, action_enum, reason_code, rationale, now, correlation_id
            )
        if action_enum is DecisionAction.REQUEST_CHANGES:
            return self._apply_changes_requested(
                case, record, staff, action_enum, reason_code, rationale, now, correlation_id
            )
        if action_enum is DecisionAction.SUPPRESS:
            return self._apply_suppression(
                case, record, staff, action_enum, reason_code, rationale, now, correlation_id
            )
        if action_enum in (DecisionAction.RESOLVE_DISPUTE, DecisionAction.DISMISS):
            return self._apply_resolution(
                case,
                record,
                staff,
                action_enum,
                reason_code,
                rationale,
                target_status_for_dispute,
                now,
                correlation_id,
            )
        return DecisionOutcome(success=False, error="unsupported_action")

    # ── Community reports (Req 7.7, 7.8, 4.6) ─────────────────────────────────

    @transaction.atomic
    def file_report(
        self,
        *,
        record_reference: UUID | None,
        category: str,
        explanation: str,
        reporter_pseudonym: str = "",
        correlation_id: UUID | None = None,
    ) -> ReportOutcome:
        """File a community report; sensitive categories suppress synchronously.

        The suppression write happens inside this transaction, so by the time
        this method returns the record is excluded from every public query
        (all public reads filter ``suppressed_at__isnull=True``).
        """
        errors = validate_report(category, explanation)
        if errors:
            raise ModerationError("; ".join(error.message for error in errors))

        record = (
            TokenBudgetRecord.public_queryset().filter(public_reference=record_reference).first()
        )
        if record is None:
            # Unknown or already-ineligible records do not reveal existence.
            raise ModerationError("The reported record could not be accepted.")

        now = self._now()
        priority = priority_for_source(ModerationSource.REPORT, category)
        sensitive = category in SENSITIVE_REPORT_CATEGORIES

        case = ModerationCase.objects.create(
            record=record,
            source=ModerationSource.REPORT,
            category=category,
            encrypted_explanation=encrypt_private_text(explanation),
            state=ModerationCaseState.OPEN,
            priority=priority,
            claimant_surrogate=uuid4() if not reporter_pseudonym else None,
            previous_verification_status=record.verification_status,
            opened_at=now,
        )

        suppressed = False
        disputed = False
        if sensitive:
            # Synchronous suppression BEFORE returning (Req 7.8).
            record.suppressed_at = now
            record.publication_state = PublicationState.QUEUED
            record.save(
                update_fields=["suppressed_at", "publication_state", "row_version", "updated_at"]
            )
            suppressed = True
        elif category == ReportCategory.INACCURATE or category == ReportCategory.ABUSIVE:
            # Ordinary dispute controls public verification status (Req 4.6).
            record.verification_status = VerificationStatus.DISPUTED
            record.save(update_fields=["verification_status", "row_version", "updated_at"])
            disputed = True

        audit_on_commit(
            AuditEntry(
                actor_surrogate=None,
                actor_role=None,
                action="COMMUNITY_REPORT_FILED",
                target_type="TokenBudgetRecord",
                target_id=record.id,
                outcome="SUCCESS",
                correlation_id=correlation_id or uuid4(),
                sanitized_metadata={
                    "case_category": category,
                    "suppressed": suppressed,
                    "reporter_pseudonym_present": bool(reporter_pseudonym),
                },
            )
        )
        return ReportOutcome(
            case_id=case.id,
            record_reference=record.public_reference,
            suppressed=suppressed,
            disputed=disputed,
        )

    # ── Decision internals ────────────────────────────────────────────────────

    def _apply_approval(
        self, case, record, staff, action_enum, reason_code, rationale, now, correlation_id
    ) -> DecisionOutcome:
        try:
            validate_publication_transition(record.publication_state, PublicationState.APPROVED)
        except Exception:
            return DecisionOutcome(success=False, case_id=case.id, error="invalid_transition")
        record.publication_state = PublicationState.APPROVED
        record.published_at = now
        record.last_reviewed_at = now
        if record.verification_status == VerificationStatus.UNVERIFIED:
            record.verification_status = VerificationStatus.VERIFIED
        record.save(
            update_fields=[
                "publication_state",
                "published_at",
                "last_reviewed_at",
                "verification_status",
                "row_version",
                "updated_at",
            ]
        )
        self._close_case_with_decision(
            case, record, staff, action_enum, reason_code, rationale, now
        )
        self._audit_decision(case, record, staff, action_enum, now, correlation_id)
        return DecisionOutcome(success=True, case_id=case.id)

    def _apply_rejection(
        self, case, record, staff, action_enum, reason_code, rationale, now, correlation_id
    ) -> DecisionOutcome:
        try:
            validate_publication_transition(record.publication_state, PublicationState.REJECTED)
        except Exception:
            return DecisionOutcome(success=False, case_id=case.id, error="invalid_transition")
        record.publication_state = PublicationState.REJECTED
        record.last_reviewed_at = now
        if record.verification_status != VerificationStatus.DISPUTED:
            record.verification_status = VerificationStatus.REJECTED
        record.save(
            update_fields=[
                "publication_state",
                "last_reviewed_at",
                "verification_status",
                "row_version",
                "updated_at",
            ]
        )
        self._close_case_with_decision(
            case, record, staff, action_enum, reason_code, rationale, now
        )
        self._audit_decision(case, record, staff, action_enum, now, correlation_id)
        return DecisionOutcome(success=True, case_id=case.id)

    def _apply_changes_requested(
        self, case, record, staff, action_enum, reason_code, rationale, now, correlation_id
    ) -> DecisionOutcome:
        try:
            validate_publication_transition(
                record.publication_state, PublicationState.CHANGES_REQUESTED
            )
        except Exception:
            return DecisionOutcome(success=False, case_id=case.id, error="invalid_transition")
        record.publication_state = PublicationState.CHANGES_REQUESTED
        record.last_reviewed_at = now
        record.save(
            update_fields=["publication_state", "last_reviewed_at", "row_version", "updated_at"]
        )
        self._close_case_with_decision(
            case, record, staff, action_enum, reason_code, rationale, now
        )
        self._audit_decision(case, record, staff, action_enum, now, correlation_id)
        return DecisionOutcome(success=True, case_id=case.id)

    def _apply_suppression(
        self, case, record, staff, action_enum, reason_code, rationale, now, correlation_id
    ) -> DecisionOutcome:
        """Moderator-initiated suppression (privacy/safety) after review."""
        record.suppressed_at = now
        record.publication_state = PublicationState.QUEUED
        record.save(
            update_fields=["suppressed_at", "publication_state", "row_version", "updated_at"]
        )
        self._close_case_with_decision(
            case, record, staff, action_enum, reason_code, rationale, now
        )
        self._audit_decision(case, record, staff, action_enum, now, correlation_id)
        return DecisionOutcome(success=True, case_id=case.id)

    def _apply_resolution(
        self,
        case,
        record,
        staff,
        action_enum,
        reason_code,
        rationale,
        target_status,
        now,
        correlation_id,
    ) -> DecisionOutcome:
        """Resolve an open dispute/dismissal to a state-valid status (Req 7.9)."""
        if target_status is None:
            return DecisionOutcome(success=False, case_id=case.id, error="target_status_required")
        try:
            validate_dispute_resolution_status(target_status)
        except Exception:
            return DecisionOutcome(success=False, case_id=case.id, error="invalid_target_status")

        record.verification_status = target_status
        record.last_reviewed_at = now
        # Restore publication for sensitive-report cases resolved in favor of
        # the record; leave rejected/withdrawn records untouched.
        if record.suppressed_at is not None and target_status != VerificationStatus.REJECTED:
            record.suppressed_at = None
            record.publication_state = PublicationState.APPROVED
            record.published_at = record.published_at or now
        record.save(
            update_fields=[
                "verification_status",
                "last_reviewed_at",
                "suppressed_at",
                "publication_state",
                "published_at",
                "row_version",
                "updated_at",
            ]
        )
        self._close_case_with_decision(
            case, record, staff, action_enum, reason_code, rationale, now
        )
        self._audit_decision(case, record, staff, action_enum, now, correlation_id)
        return DecisionOutcome(success=True, case_id=case.id)

    @staticmethod
    def _close_case_with_decision(
        case, record, staff, action_enum, reason_code, rationale, now
    ) -> None:
        ModerationDecision.objects.create(
            case=case,
            action=action_enum.value,
            reason_code=reason_code[:100],
            encrypted_rationale=encrypt_private_text(rationale) if rationale else None,
            staff=staff,
            resulting_publication_state=record.publication_state,
            resulting_verification_status=record.verification_status,
            decided_at=now,
        )
        case.state = ModerationCaseState.RESOLVED
        case.assigned_staff = staff
        case.lease_expires_at = None
        case.save(
            update_fields=[
                "state",
                "assigned_staff",
                "lease_expires_at",
                "row_version",
                "updated_at",
            ]
        )

    @staticmethod
    def _audit_decision(case, record, staff, action_enum, now, correlation_id) -> None:
        audit_on_commit(
            AuditEntry(
                actor_surrogate=staff.id,
                actor_role=staff.role,
                action=f"MODERATION_{action_enum.value}",
                target_type="TokenBudgetRecord",
                target_id=record.id,
                outcome="SUCCESS",
                correlation_id=correlation_id or uuid4(),
                sanitized_metadata={
                    "case_source": case.source,
                    "case_category": case.category,
                },
            )
        )


def generate_report_pseudonym() -> str:
    """A random pseudonym for anonymous reporters; never derived from identity."""
    return f"reporter-{secrets.token_hex(6)}"
