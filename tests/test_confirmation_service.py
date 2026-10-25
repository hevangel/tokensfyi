"""Tests for atomic, idempotent submission confirmation (Req 5.7, 5.8, 7.1, 8.1)."""

from __future__ import annotations

import pytest

from token_budget_platform.adapters.django.models import (
    ContributorContact,
    ManagementCapability,
    ModerationCase,
    OutboxEvent,
    PublicationState,
    RecordVersion,
    TokenBudgetRecord,
    VerificationStatus,
)
from token_budget_platform.application.catalog.public_projection_compat import build_preview
from token_budget_platform.application.submission.confirmation import (
    ConfirmationError,
    SubmissionConfirmationService,
    preview_confirmation,
)

pytestmark = pytest.mark.django_db


def _payload(**overrides):
    from tests.test_accessibility_templates import _create_public_record  # reuse vocabulary factory

    _create_public_record()
    from token_budget_platform.adapters.django.models import ControlledValue

    def value(kind, code):
        row = ControlledValue.objects.filter(kind=kind, code=code).first()
        assert row is not None
        return str(row.id)

    payload = {
        "organization_name": "Confirmation Test Org",
        "industry": "Technology",
        "size_band_id": value("SIZE_BAND", "SMALL"),
        "region_id": value("REGION", "EU"),
        "model_provider_id": value("MODEL_PROVIDER", "OPENAI"),
        "ai_product_id": value("AI_PRODUCT", "GPT4O"),
        "workload_category_id": value("WORKLOAD_CATEGORY", "RESEARCH"),
        "budget_scope": "TEAM",
        "token_budget": "424242",
        "reporting_start": "2026-01-01",
        "reporting_end": "2026-06-30",
        "reporting_timezone": "UTC",
        "source_date": "2026-07-10",
        "methodology_notes": "Test confirmation",
        "contributor_attribution": "ANONYMOUS",
    }
    payload.update(overrides)
    return payload


def test_confirmation_creates_all_atoms_atomically() -> None:
    service = SubmissionConfirmationService()
    result = service.confirm_payload(payload=_payload(), idempotency_key="")

    record = TokenBudgetRecord.objects.get(id=result.record_id)
    assert record.publication_state == PublicationState.QUEUED
    assert record.verification_status == VerificationStatus.UNVERIFIED
    # Immutable initial version
    versions = RecordVersion.objects.filter(record=record)
    assert versions.count() == 1
    version_row = versions.first()
    assert version_row is not None
    assert version_row.version == 1
    # Moderation queue case
    assert ModerationCase.objects.filter(record=record, source="SUBMISSION").exists()
    # Capability digest persisted; raw token only in the result
    capability = ManagementCapability.objects.filter(record=record)
    assert capability.count() == 1
    capability_row = capability.first()
    assert capability_row is not None
    assert capability_row.token_digest != result.management_token
    assert len(result.management_token) == 64
    # Outbox events
    event_types = set(
        OutboxEvent.objects.filter(aggregate_id=record.id).values_list("event_type", flat=True)
    )
    assert "RECALCULATE_METRICS" in event_types


def test_confirmation_is_idempotent_on_same_key() -> None:
    service = SubmissionConfirmationService()
    key = "same-key"
    first = service.confirm_payload(payload=_payload(), idempotency_key=key)
    second = service.confirm_payload(payload=_payload(), idempotency_key=key)

    assert second.replayed is True
    assert second.record_id == first.record_id
    # Exactly one record was created for this organization/payload pair under the key
    assert TokenBudgetRecord.objects.filter(organization__name="Confirmation Test Org").count() == 1
    assert second.management_token == ""


def test_confirmation_binds_contact_only_when_provided() -> None:
    service = SubmissionConfirmationService()
    with_contact = service.confirm_payload(
        payload=_payload(contact_email="person@example.com"), idempotency_key=""
    )
    assert ContributorContact.objects.filter(record_id=with_contact.record_id).exists()

    without_contact = service.confirm_payload(
        payload=_payload(organization_name="No Contact Org"), idempotency_key=""
    )
    assert not ContributorContact.objects.filter(record_id=without_contact.record_id).exists()


def test_confirmation_registers_audit_on_commit() -> None:
    from django.db import transaction
    from django.test import TestCase

    service = SubmissionConfirmationService()
    case = TestCase()
    with case.captureOnCommitCallbacks(execute=True) as callbacks:
        with transaction.atomic():
            service.confirm_payload(payload=_payload(), idempotency_key="")
    assert len(callbacks) >= 1  # the audit append fires after commit


def test_invalid_payload_reference_raises_safe_error() -> None:
    service = SubmissionConfirmationService()
    with pytest.raises(ConfirmationError):
        service.confirm_payload(
            payload=_payload(model_provider_id="00000000-0000-0000-0000-000000000000"),
            idempotency_key="",
        )


def test_preview_matches_public_projection_fields() -> None:
    preview = preview_confirmation(
        {
            "organization_name": "Preview Org",
            "industry": "Retail",
            "size_band_label": "1-50",
            "region_label": "Europe",
            "model_provider_label": "OpenAI",
            "ai_product_label": "GPT-4o",
            "workload_category_label": "Research",
            "budget_scope": "TEAM",
            "token_budget": "1000",
            "reporting_start": "2026-01-01",
            "reporting_end": "2026-06-30",
            "source_date": "2026-07-01",
        }
    )
    expected_keys = set(build_preview({}).keys())
    assert set(preview.keys()) == expected_keys
    assert preview["organization_name"] == "Preview Org"
    assert preview["verification_status"] == "UNVERIFIED"
    assert preview["is_preview"] is True
