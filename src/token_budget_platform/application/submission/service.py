"""Application service for submission drafts and validation.

Creates server-side drafts, validates steps progressively, and coordinates
between the pure domain SubmissionValidator and persistence.  The same
validator instance is shared by HTML forms, JSON APIs, and preview.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    ControlledValue,
    ControlledValueKind,
    SubmissionDraft,
)
from token_budget_platform.domain.ports import Clock
from token_budget_platform.domain.submission.types import DraftResult
from token_budget_platform.domain.submission.validator import (
    SubmissionValidator,
    ValidationResult,
)

# Default draft expiry: 30 days from creation
DRAFT_EXPIRY_DAYS = 30

# Capability token length in bytes (256 bits)
CAPABILITY_TOKEN_BYTES = 32


class SubmissionError(Exception):
    """Base error for submission operations."""


class DraftNotFoundError(SubmissionError):
    """Raised when a draft cannot be found or the capability is invalid."""


class DraftExpiredError(SubmissionError):
    """Raised when operating on an expired draft."""


class SubmissionService:
    """Manages submission drafts, progressive validation, and preview generation.

    Uses the same SubmissionValidator for all contexts (HTML, API, preview,
    persistence) to guarantee consistent validation behavior.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock.now()
        return timezone.now()

    def _today(self) -> date:
        return self._now().date()

    def _active_controlled_value_ids(self) -> dict[str, set[str]]:
        """Load currently active controlled-value IDs grouped by kind."""
        now = self._now()
        from django.db.models import Q

        values = ControlledValue.objects.filter(
            kind__in=[k for k, _ in ControlledValueKind.choices],
            active_from__lte=now,
        ).filter(Q(active_to__isnull=True) | Q(active_to__gt=now))

        result: dict[str, set[str]] = {}
        for v in values:
            result.setdefault(v.kind, set()).add(str(v.id))
        return result

    def _vocabulary_versions(self) -> dict[str, int]:
        """Return the maximum active vocabulary version for each kind."""
        now = self._now()
        from django.db.models import Q

        values = ControlledValue.objects.filter(
            kind__in=[k for k, _ in ControlledValueKind.choices],
            active_from__lte=now,
        ).filter(Q(active_to__isnull=True) | Q(active_to__gt=now))

        versions: dict[str, int] = {}
        for v in values:
            current = versions.get(v.kind, 0)
            if v.vocabulary_version > current:
                versions[v.kind] = v.vocabulary_version
        return versions

    def _make_validator(self) -> SubmissionValidator:
        """Create a validator with the current active controlled values."""
        return SubmissionValidator(
            active_controlled_value_ids=self._active_controlled_value_ids(),
            reference_date=self._today(),
        )

    @staticmethod
    def _hash_capability(token: str) -> str:
        """Compute a SHA-256 digest of a capability token for storage."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @transaction.atomic
    def create_draft(self, payload: dict[str, Any]) -> DraftResult:
        """Create a new server-side draft with validation.

        Validates the provided payload, stores the draft with an expiry,
        and returns a capability token that allows future draft access.
        The raw capability token is returned once and never stored.
        """
        validator = self._make_validator()
        validation = validator.validate(payload)
        vocab_versions = self._vocabulary_versions()

        # Generate the unguessable capability token
        capability_token = secrets.token_urlsafe(CAPABILITY_TOKEN_BYTES)
        capability_digest = self._hash_capability(capability_token)

        now = self._now()
        expires_at = now + timedelta(days=DRAFT_EXPIRY_DAYS)

        draft = SubmissionDraft.objects.create(
            payload=payload,
            capability_digest=capability_digest,
            expires_at=expires_at,
            vocabulary_versions=vocab_versions,
            validation_version=1,
            validation_result={
                "valid": validation.valid,
                "errors": [
                    {"field": e.field, "code": e.code, "message": e.message}
                    for e in validation.errors
                ],
            },
        )

        return DraftResult(
            draft_id=draft.id,
            capability_token=capability_token,
            expires_at=expires_at,
            validation_result={
                "valid": validation.valid,
                "errors": [
                    {"field": e.field, "code": e.code, "message": e.message}
                    for e in validation.errors
                ],
            },
            vocabulary_versions=vocab_versions,
        )

    @transaction.atomic
    def update_draft(
        self,
        draft_id: UUID,
        capability_token: str,
        payload: dict[str, Any],
    ) -> DraftResult:
        """Update an existing draft with new payload data.

        Validates the capability token, merges updates into the stored payload
        while preserving valid sibling values, re-validates, and persists.
        """
        draft = self._load_draft(draft_id, capability_token)

        # Merge: new payload fields override existing ones
        merged = {**draft.payload, **payload}

        validator = self._make_validator()
        validation = validator.validate(merged)
        vocab_versions = self._vocabulary_versions()

        draft.payload = merged
        draft.validation_result = {
            "valid": validation.valid,
            "errors": [
                {"field": e.field, "code": e.code, "message": e.message} for e in validation.errors
            ],
        }
        draft.validation_version += 1
        draft.vocabulary_versions = vocab_versions
        draft.save()

        return DraftResult(
            draft_id=draft.id,
            capability_token=capability_token,
            expires_at=draft.expires_at,
            validation_result=draft.validation_result,
            vocabulary_versions=vocab_versions,
        )

    @transaction.atomic
    def update_draft_step(
        self,
        draft_id: UUID,
        capability_token: str,
        step: str,
        step_payload: dict[str, Any],
    ) -> DraftResult:
        """Validate and update a single step of a multi-step form.

        Merges step data into the draft, validates just that step, and returns
        step-specific errors. The full validation state is also recomputed.
        """
        draft = self._load_draft(draft_id, capability_token)

        # Merge step data into stored payload
        merged = {**draft.payload, **step_payload}

        validator = self._make_validator()
        # Validate just this step for immediate feedback
        step_validation = validator.validate_step(step, merged)
        # Also do full validation for overall state
        full_validation = validator.validate(merged)
        vocab_versions = self._vocabulary_versions()

        draft.payload = merged
        draft.validation_result = {
            "valid": full_validation.valid,
            "errors": [
                {"field": e.field, "code": e.code, "message": e.message}
                for e in full_validation.errors
            ],
        }
        draft.validation_version += 1
        draft.vocabulary_versions = vocab_versions
        draft.save()

        return DraftResult(
            draft_id=draft.id,
            capability_token=capability_token,
            expires_at=draft.expires_at,
            validation_result={
                "valid": step_validation.valid,
                "step_errors": [
                    {"field": e.field, "code": e.code, "message": e.message}
                    for e in step_validation.errors
                ],
                "overall_valid": full_validation.valid,
                "overall_errors": [
                    {"field": e.field, "code": e.code, "message": e.message}
                    for e in full_validation.errors
                ],
            },
            vocabulary_versions=vocab_versions,
        )

    def get_draft(self, draft_id: UUID, capability_token: str) -> dict[str, Any]:
        """Retrieve draft payload and validation state for display."""
        draft = self._load_draft(draft_id, capability_token)
        return {
            "draft_id": str(draft.id),
            "payload": draft.payload,
            "expires_at": draft.expires_at.isoformat(),
            "validation_result": draft.validation_result,
            "vocabulary_versions": draft.vocabulary_versions,
        }

    def validate_full(self, payload: dict[str, Any]) -> ValidationResult:
        """Validate a payload without persisting — used for preview gating."""
        validator = self._make_validator()
        return validator.validate(payload)

    def _load_draft(self, draft_id: UUID, capability_token: str) -> SubmissionDraft:
        """Load and verify a draft by ID and capability token."""
        try:
            draft = SubmissionDraft.objects.select_for_update().get(id=draft_id)
        except SubmissionDraft.DoesNotExist:
            raise DraftNotFoundError("Draft not found.") from None

        # Verify capability (constant-time comparison via digest match)
        expected_digest = draft.capability_digest
        provided_digest = self._hash_capability(capability_token)
        if not secrets.compare_digest(expected_digest, provided_digest):
            raise DraftNotFoundError("Invalid draft capability.")

        # Check expiry
        if draft.expires_at < self._now():
            raise DraftExpiredError("Draft has expired.")

        return draft
