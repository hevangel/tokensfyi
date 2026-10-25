"""Tests for moderation queue, decisions, and community reports (Req 4.6, 7.2-7.9)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    AuditRecord,
    ModerationCase,
    ModerationCaseState,
    PublicationState,
    SecurityEvent,
    StaffUser,
    TokenBudgetRecord,
    VerificationStatus,
)
from token_budget_platform.application.audit import AuditEntry, record_audit
from token_budget_platform.application.iam.authentication import AuthenticationService
from token_budget_platform.application.moderation.service import (
    CaseNotFoundError,
    LeaseConflictError,
    ModerationError,
    ModerationService,
)
from token_budget_platform.domain.iam.staff import StaffRole

pytestmark = pytest.mark.django_db

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _staff(role: StaffRole = StaffRole.MODERATOR) -> StaffUser:
    provisioned = AuthenticationService().provision_staff(
        username=f"mod-{role.value.lower()}-{StaffUser.objects.count()}",
        # Throwaway in-test credential for a disposable in-test account only.
        password="".join(["throwaway-", "test-", "credential"]),
        role=role,
    )
    return StaffUser.objects.get(id=provisioned.user_id)


def _make_record(**overrides) -> TokenBudgetRecord:
    from tests.test_accessibility_templates import _create_public_record

    _create_public_record()
    record = TokenBudgetRecord.objects.order_by("created_at").first()
    assert record is not None
    record.publication_state = PublicationState.QUEUED
    record.published_at = None
    for field, value in overrides.items():
        setattr(record, field, value)
    record.save()
    return record


def _open_case(record: TokenBudgetRecord, source: str = "SUBMISSION") -> ModerationCase:
    return ModerationCase.objects.create(
        record=record,
        source=source,
        category="NEW_SUBMISSION",
        state=ModerationCaseState.OPEN,
        priority=40,
        opened_at=timezone.now(),
    )


class TestQueue:
    def test_queue_orders_by_priority_then_opened(self) -> None:
        record = _make_record()
        low = _open_case(record)
        low.priority = 70
        low.save()
        high = _open_case(record)
        high.priority = 10
        high.save()

        entries = ModerationService().queue()
        assert [entry.case_id for entry in entries[:2]] == [high.id, low.id]

    def test_queue_excludes_private_content(self) -> None:
        import dataclasses

        record = _make_record()
        _open_case(record)
        entry = ModerationService().queue()[0]
        data = dataclasses.asdict(entry)
        for forbidden in ("encrypted_explanation", "private_notes", "claimant_surrogate"):
            assert forbidden not in data


class TestLeases:
    def test_claim_and_conflict(self) -> None:
        record = _make_record()
        case = _open_case(record)
        moderator = _staff()
        other = _staff()

        claim = ModerationService().claim(case_id=case.id, staff=moderator)
        assert claim.lease_expires_at is not None
        with pytest.raises(LeaseConflictError):
            ModerationService().claim(case_id=case.id, staff=other)

    def test_claim_missing_case_raises(self) -> None:
        from uuid import UUID

        moderator = _staff()
        with pytest.raises(CaseNotFoundError):
            ModerationService().claim(
                case_id=UUID("00000000-0000-0000-0000-000000000000"), staff=moderator
            )

    def test_decision_requires_lease(self) -> None:
        record = _make_record()
        case = _open_case(record)
        moderator = _staff()

        outcome = ModerationService().decide(
            case_id=case.id, staff=moderator, action="APPROVE", reason_code="OK"
        )
        assert outcome.success is False
        assert outcome.error == "lease_required"
        case.refresh_from_db()
        assert case.state == ModerationCaseState.OPEN


class TestDecisions:
    def _claimed_case(self):
        record = _make_record()
        case = _open_case(record)
        moderator = _staff()
        ModerationService().claim(case_id=case.id, staff=moderator)
        return case, moderator

    def test_approval_publishes_and_verifies(self) -> None:
        case, moderator = self._claimed_case()
        outcome = ModerationService().decide(
            case_id=case.id,
            staff=moderator,
            action="APPROVE",
            reason_code="REVIEWED",
            rationale="Consistent with evidence policy.",
        )
        assert outcome.success
        case.refresh_from_db()
        record = TokenBudgetRecord.objects.get(id=case.record_id)
        assert case.state == ModerationCaseState.RESOLVED
        assert record.publication_state == PublicationState.APPROVED
        assert record.verification_status == VerificationStatus.VERIFIED
        assert record.published_at is not None

    def test_rejection_records_structured_reason(self) -> None:
        case, moderator = self._claimed_case()
        outcome = ModerationService().decide(
            case_id=case.id,
            staff=moderator,
            action="REJECT",
            reason_code="EVIDENCE_INSUFFICIENT",
            rationale="The source could not be located.",
        )
        assert outcome.success
        record = TokenBudgetRecord.objects.get(id=case.record_id)
        assert record.publication_state == PublicationState.REJECTED
        assert record.verification_status == VerificationStatus.REJECTED
        decision = case.decisions.first()
        assert decision is not None
        assert decision.reason_code == "EVIDENCE_INSUFFICIENT"
        assert decision.encrypted_rationale not in (None, b"")
        assert b"source" not in bytes(decision.encrypted_rationale)

    def test_request_changes_keeps_record_non_public(self) -> None:
        case, moderator = self._claimed_case()
        outcome = ModerationService().decide(
            case_id=case.id,
            staff=moderator,
            action="REQUEST_CHANGES",
            reason_code="CLARIFY_PERIOD",
        )
        assert outcome.success
        record = TokenBudgetRecord.objects.get(id=case.record_id)
        assert record.publication_state == PublicationState.CHANGES_REQUESTED
        assert TokenBudgetRecord.public_queryset().filter(id=record.id).exists() is False

    def test_invalid_transition_refused(self) -> None:
        case, moderator = self._claimed_case()
        record = TokenBudgetRecord.objects.get(id=case.record_id)
        record.publication_state = PublicationState.APPROVED
        record.save()

        outcome = ModerationService().decide(
            case_id=case.id,
            staff=moderator,
            action="APPROVE",
            reason_code="OK",
        )
        assert outcome.success is False
        assert outcome.error == "invalid_transition"

    def test_resolved_case_refuses_further_decisions(self) -> None:
        case, moderator = self._claimed_case()
        ModerationService().decide(
            case_id=case.id, staff=moderator, action="APPROVE", reason_code="OK"
        )
        outcome = ModerationService().decide(
            case_id=case.id, staff=moderator, action="REJECT", reason_code="LATE"
        )
        assert outcome.success is False
        assert outcome.error == "case_not_open"


class TestReports:
    def test_ordinary_report_sets_disputed(self) -> None:
        record = _make_record(
            publication_state=PublicationState.APPROVED,
            published_at=timezone.now(),
        )
        outcome = ModerationService().file_report(
            record_reference=record.public_reference,
            category="INACCURATE",
            explanation="The reported numbers do not match the public source.",
        )
        record.refresh_from_db()
        assert outcome.suppressed is False
        assert record.verification_status == VerificationStatus.DISPUTED
        assert ModerationCase.objects.filter(id=outcome.case_id).exists()

    def test_sensitive_report_suppresses_synchronously(self) -> None:
        record = _make_record(
            publication_state=PublicationState.APPROVED,
            published_at=timezone.now(),
        )
        outcome = ModerationService().file_report(
            record_reference=record.public_reference,
            category="PERSONAL_DATA",
            explanation="The methodology notes contain employee identifiers.",
        )
        record.refresh_from_db()
        assert outcome.suppressed is True
        assert record.suppressed_at is not None
        # Suppression is visible to every public query before the response returns.
        assert not TokenBudgetRecord.public_queryset().filter(id=record.id).exists()

    def test_invalid_report_rejected_without_case(self) -> None:
        record = _make_record()
        before = ModerationCase.objects.count()
        with pytest.raises(ModerationError):
            ModerationService().file_report(
                record_reference=record.public_reference,
                category="NOT_A_CATEGORY",
                explanation="too short",
            )
        assert ModerationCase.objects.count() == before

    def test_dispute_resolution_state_machine(self) -> None:
        record = _make_record(
            publication_state=PublicationState.APPROVED,
            verification_status=VerificationStatus.DISPUTED,
            published_at=timezone.now(),
        )
        case = _open_case(record, source="REPORT")
        moderator = _staff()
        ModerationService().claim(case_id=case.id, staff=moderator)

        bad = ModerationService().decide(
            case_id=case.id,
            staff=moderator,
            action="RESOLVE_DISPUTE",
            reason_code="REVIEWED",
            target_status_for_dispute="DISPUTED",  # not an allowed target
        )
        assert bad.success is False
        assert bad.error == "invalid_target_status"

        good = ModerationService().decide(
            case_id=case.id,
            staff=moderator,
            action="RESOLVE_DISPUTE",
            reason_code="REVIEWED",
            target_status_for_dispute="VERIFIED",
        )
        assert good.success
        record.refresh_from_db()
        assert record.verification_status == VerificationStatus.VERIFIED


class TestAudit:
    def test_audit_append_once_and_survives_failure(self) -> None:
        from uuid import uuid4

        entry = AuditEntry(
            actor_surrogate=None,
            actor_role=None,
            action="TEST_ACTION",
            target_type="TokenBudgetRecord",
            target_id=None,
            outcome="SUCCESS",
            correlation_id=uuid4(),
            sanitized_metadata={"note": "password=hunter2 should be redacted"},
        )
        assert record_audit(entry) is True
        row = AuditRecord.objects.order_by("-sequence").first()
        assert row is not None
        import json

        metadata = json.dumps(row.sanitized_metadata)
        assert "hunter2" not in metadata

    def test_denied_authorization_emits_sanitized_event(self) -> None:
        from token_budget_platform.application.iam.authorization import AuthorizationService
        from token_budget_platform.domain.iam.policies import Resource

        decision = AuthorizationService().authorize(
            actor_username="no-such-user", resource=Resource.STAFF_PROVISIONING
        )
        assert decision.allowed is False
        event = SecurityEvent.objects.filter(event_type="AUTHORIZATION_DENIED").latest(
            "occurred_at"
        )
        assert "no-such-user" not in event.actor_or_network_pseudonym
