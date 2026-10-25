"""Tests for contributor management: status, amendment, withdrawal (Req 8.2-8.6, 12.4)."""

from __future__ import annotations

import pytest

from token_budget_platform.adapters.django.models import (
    ManagementCapability,
    ManagementSession,
    ModerationCase,
    ModerationCaseState,
    PublicationState,
    RecordVersion,
    TokenBudgetRecord,
)
from token_budget_platform.application.submission.management_service import (
    ContributorManagementService,
    ManagementError,
    RecordNotFoundError,
)

pytestmark = pytest.mark.django_db


def _record_with_capability(publication_state: str):
    from decimal import Decimal

    from tests.test_accessibility_templates import _create_public_record
    from token_budget_platform.adapters.django.models import Organization
    from token_budget_platform.application.submission.confirmation import (
        management_capability_service,
    )

    _create_public_record()

    organization = Organization.objects.filter(slug="accessibility-test-org").first()
    assert organization is not None
    record = TokenBudgetRecord.objects.filter(organization=organization).first()
    assert record is not None
    record.publication_state = publication_state
    record.token_budget = Decimal("700000")
    record.save()

    service = management_capability_service()
    issued = service.issue_capability(record.id)
    return record, issued.raw_token


class TestStatus:
    def test_owner_sees_full_status_with_feedback(self) -> None:
        record, _ = _record_with_capability(PublicationState.CHANGES_REQUESTED)
        from django.utils import timezone

        from token_budget_platform.adapters.django.models import ModerationDecision, StaffUser
        from token_budget_platform.application.iam.authentication import AuthenticationService
        from token_budget_platform.domain.iam.staff import StaffRole

        case = ModerationCase.objects.create(
            record=record,
            source="SUBMISSION",
            category="NEW_SUBMISSION",
            state=ModerationCaseState.OPEN,
            priority=40,
            opened_at=timezone.now(),
        )
        provisioned = AuthenticationService().provision_staff(
            username=f"rev-{StaffUser.objects.count()}",
            # Throwaway in-test credential for a disposable in-test account only.
            password="".join(["throwaway-", "test-", "credential"]),
            role=StaffRole.MODERATOR,
        )
        staff = StaffUser.objects.get(id=provisioned.user_id)
        ModerationDecision.objects.create(
            case=case,
            action="REQUEST_CHANGES",
            reason_code="CLARIFY_SOURCE",
            staff=staff,
            resulting_publication_state=record.publication_state,
            resulting_verification_status=record.verification_status,
            decided_at=timezone.now(),
        )
        status = ContributorManagementService().status(record_id=record.id)
        assert status["publication_state"] == PublicationState.CHANGES_REQUESTED
        assert status["moderation_feedback"][0]["reason_code"] == "CLARIFY_SOURCE"

    def test_unknown_record_raises(self) -> None:
        from uuid import uuid4

        with pytest.raises(RecordNotFoundError):
            ContributorManagementService().status(record_id=uuid4())


class TestAmendment:
    def test_amendment_appends_version_and_reenters_review(self) -> None:
        record, _ = _record_with_capability(PublicationState.APPROVED)
        service = ContributorManagementService()
        result = service.amend(record_id=record.id, changes={"token_budget": "800000"})

        assert result.new_version == 2
        record.refresh_from_db()
        assert record.publication_state == PublicationState.QUEUED
        assert record.token_budget == 800000
        amendment_version = RecordVersion.objects.filter(record=record, version=2).first()
        assert amendment_version is not None
        assert amendment_version.changed_fields == ["token_budget"]
        assert ModerationCase.objects.filter(record=record, state=ModerationCaseState.OPEN).exists()

    def test_no_op_amendment_rejected(self) -> None:
        record, _ = _record_with_capability(PublicationState.APPROVED)
        with pytest.raises(ManagementError):
            ContributorManagementService().amend(
                record_id=record.id, changes={"token_budget": "700000"}
            )

    def test_unknown_field_rejected(self) -> None:
        record, _ = _record_with_capability(PublicationState.APPROVED)
        with pytest.raises(ManagementError):
            ContributorManagementService().amend(
                record_id=record.id, changes={"verification_status": "VERIFIED"}
            )


class TestWithdrawal:
    def test_withdrawal_removes_from_public_and_revokes_access(self) -> None:
        record, token = _record_with_capability(PublicationState.APPROVED)
        service = ContributorManagementService()
        outcome = service.withdraw(record_id=record.id)

        record.refresh_from_db()
        assert record.publication_state == PublicationState.WITHDRAWN
        assert record.withdrawn_at is not None
        assert not TokenBudgetRecord.public_queryset().filter(id=record.id).exists()
        # All capabilities and sessions revoked
        assert (
            ManagementCapability.objects.filter(record=record, revoked_at__isnull=True).count() == 0
        )
        assert ManagementSession.objects.filter(record=record, revoked_at__isnull=True).count() == 0
        assert outcome.removal_deadline > outcome.withdrawn_at

    def test_double_withdrawal_rejected(self) -> None:
        record, _ = _record_with_capability(PublicationState.APPROVED)
        service = ContributorManagementService()
        service.withdraw(record_id=record.id)
        with pytest.raises(ManagementError):
            service.withdraw(record_id=record.id)
