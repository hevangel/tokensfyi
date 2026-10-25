"""Integration tests for SubmissionService draft creation.

Validates Requirements 5.1, 5.4, 5.5, 9.1, 9.2:
- Draft creation with valid payload stores encrypted draft and returns capability
- Draft capability token validates correctly (constant-time comparison)
- Draft expiry is enforced
- Step-by-step validation works through the service
- Invalid capabilities are rejected without revealing draft existence
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    ControlledValue,
    ControlledValueKind,
    SubmissionDraft,
)
from token_budget_platform.application.submission.service import (
    DraftExpiredError,
    DraftNotFoundError,
    SubmissionService,
)


@pytest.fixture
def now():
    return timezone.now()


@pytest.fixture
def controlled_values(now):
    """Create a minimal set of active controlled values for testing."""
    values = {}
    for kind, code, label in [
        (ControlledValueKind.MODEL_PROVIDER, "openai", "OpenAI"),
        (ControlledValueKind.AI_PRODUCT, "chatgpt", "ChatGPT Enterprise"),
        (ControlledValueKind.WORKLOAD_CATEGORY, "dev", "Software Development"),
        (ControlledValueKind.SIZE_BAND, "medium", "51-200 employees"),
        (ControlledValueKind.REGION, "us-east", "US East"),
    ]:
        cv = ControlledValue.objects.create(
            kind=kind,
            code=code,
            label=label,
            normalized_label=label.lower(),
            active_from=now - timedelta(days=30),
            vocabulary_version=1,
        )
        values[kind] = str(cv.id)
    return values


@pytest.fixture
def valid_payload(controlled_values):
    """A minimal valid submission payload."""
    return {
        "organization_name": "Acme Corp",
        "industry": "Technology",
        "size_band_id": controlled_values[ControlledValueKind.SIZE_BAND],
        "region_id": controlled_values[ControlledValueKind.REGION],
        "model_provider_id": controlled_values[ControlledValueKind.MODEL_PROVIDER],
        "ai_product_id": controlled_values[ControlledValueKind.AI_PRODUCT],
        "workload_category_id": controlled_values[ControlledValueKind.WORKLOAD_CATEGORY],
        "budget_scope": "TEAM",
        "seat_count": 10,
        "token_budget": "1000000",
        "reporting_start": "2025-01-01",
        "reporting_end": "2025-03-31",
        "reporting_timezone": "UTC",
        "source_date": "2025-06-01",
        "contributor_attribution": "ANONYMOUS",
    }


@pytest.fixture
def service():
    return SubmissionService()


@pytest.mark.django_db
class TestDraftCreation:
    """Draft creation stores validation state and returns a capability token."""

    def test_create_draft_returns_result(self, service, valid_payload):
        result = service.create_draft(valid_payload)

        assert result.draft_id is not None
        assert result.capability_token is not None
        assert len(result.capability_token) > 20  # URL-safe base64 of 32 bytes
        assert result.validation_result["valid"] is True
        assert result.vocabulary_versions is not None

    def test_create_draft_persists_to_database(self, service, valid_payload):
        result = service.create_draft(valid_payload)

        draft = SubmissionDraft.objects.get(id=result.draft_id)
        assert draft.payload == valid_payload
        assert draft.validation_result["valid"] is True
        assert draft.vocabulary_versions != {}

    def test_create_draft_with_invalid_payload_stores_errors(self, service, controlled_values):
        payload = {
            "organization_name": "",
            "industry": "Tech",
            "size_band_id": controlled_values[ControlledValueKind.SIZE_BAND],
            "region_id": controlled_values[ControlledValueKind.REGION],
            "model_provider_id": controlled_values[ControlledValueKind.MODEL_PROVIDER],
            "ai_product_id": controlled_values[ControlledValueKind.AI_PRODUCT],
            "workload_category_id": controlled_values[ControlledValueKind.WORKLOAD_CATEGORY],
            "budget_scope": "TEAM",
            "token_budget": "abc",
            "reporting_start": "2025-01-01",
            "reporting_end": "2025-03-31",
            "reporting_timezone": "UTC",
            "source_date": "2025-06-01",
        }
        result = service.create_draft(payload)

        assert result.draft_id is not None
        assert result.validation_result["valid"] is False
        assert len(result.validation_result["errors"]) > 0

    def test_draft_expiry_is_set(self, service, valid_payload):
        result = service.create_draft(valid_payload)
        draft = SubmissionDraft.objects.get(id=result.draft_id)

        # Expiry should be approximately 30 days from now
        expected_expiry = timezone.now() + timedelta(days=30)
        assert abs((draft.expires_at - expected_expiry).total_seconds()) < 60

    def test_capability_token_not_stored_raw(self, service, valid_payload):
        """The raw capability token is never stored; only its hash."""
        result = service.create_draft(valid_payload)
        draft = SubmissionDraft.objects.get(id=result.draft_id)

        # The stored digest should NOT equal the raw token
        assert draft.capability_digest != result.capability_token
        # It should be a hex digest (64 chars for SHA-256)
        assert len(draft.capability_digest) == 64


@pytest.mark.django_db
class TestDraftAccess:
    """Draft access requires valid capability token."""

    def test_get_draft_with_valid_token(self, service, valid_payload):
        create_result = service.create_draft(valid_payload)
        data = service.get_draft(create_result.draft_id, create_result.capability_token)

        assert data["payload"] == valid_payload
        assert data["validation_result"]["valid"] is True

    def test_get_draft_with_invalid_token_rejected(self, service, valid_payload):
        create_result = service.create_draft(valid_payload)

        with pytest.raises(DraftNotFoundError):
            service.get_draft(create_result.draft_id, "wrong-token")

    def test_get_draft_nonexistent_id_rejected(self, service, valid_payload):
        with pytest.raises(DraftNotFoundError):
            service.get_draft(uuid4(), "any-token")

    def test_get_expired_draft_rejected(self, service, valid_payload):
        create_result = service.create_draft(valid_payload)

        # Force expiry in the past
        draft = SubmissionDraft.objects.get(id=create_result.draft_id)
        draft.expires_at = timezone.now() - timedelta(hours=1)
        draft.save()

        with pytest.raises(DraftExpiredError):
            service.get_draft(create_result.draft_id, create_result.capability_token)


@pytest.mark.django_db
class TestDraftUpdate:
    """Updating drafts merges payload and re-validates."""

    def test_update_draft_merges_payload(self, service, valid_payload):
        create_result = service.create_draft(valid_payload)

        service.update_draft(
            create_result.draft_id,
            create_result.capability_token,
            {"organization_name": "Beta Inc"},
        )

        # Name should be updated, rest preserved
        draft = SubmissionDraft.objects.get(id=create_result.draft_id)
        assert draft.payload["organization_name"] == "Beta Inc"
        assert draft.payload["industry"] == "Technology"

    def test_update_draft_revalidates(self, service, valid_payload):
        create_result = service.create_draft(valid_payload)

        # Introduce an invalid value
        update_result = service.update_draft(
            create_result.draft_id,
            create_result.capability_token,
            {"token_budget": "abc"},
        )

        assert update_result.validation_result["valid"] is False
        errors = update_result.validation_result["errors"]
        assert any(e["field"] == "token_budget" for e in errors)

    def test_update_draft_increments_version(self, service, valid_payload):
        create_result = service.create_draft(valid_payload)
        draft_before = SubmissionDraft.objects.get(id=create_result.draft_id)
        version_before = draft_before.validation_version

        service.update_draft(
            create_result.draft_id,
            create_result.capability_token,
            {"organization_name": "Updated Corp"},
        )

        draft_after = SubmissionDraft.objects.get(id=create_result.draft_id)
        assert draft_after.validation_version == version_before + 1


@pytest.mark.django_db
class TestStepValidation:
    """Step-by-step validation through the service."""

    def test_update_step_validates_single_step(self, service, valid_payload):
        create_result = service.create_draft(valid_payload)

        step_result = service.update_draft_step(
            create_result.draft_id,
            create_result.capability_token,
            "organization",
            {"organization_name": "New Corp", "industry": "Finance"},
        )

        # Step validation should be valid (org fields are fine)
        assert step_result.validation_result["valid"] is True

    def test_update_step_reports_step_errors(self, service, valid_payload):
        create_result = service.create_draft(valid_payload)

        step_result = service.update_draft_step(
            create_result.draft_id,
            create_result.capability_token,
            "organization",
            {"organization_name": "", "industry": ""},
        )

        # Step errors for the organization step
        assert step_result.validation_result["valid"] is False
        step_errors = step_result.validation_result["step_errors"]
        assert any(e["field"] == "organization_name" for e in step_errors)
        assert any(e["field"] == "industry" for e in step_errors)
