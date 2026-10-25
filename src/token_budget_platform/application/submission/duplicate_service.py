"""Application service for versioned duplicate detection.

Coordinates between the pure-domain DuplicateDetector and the persistence
layer (TokenBudgetRecord) to check for and flag duplicates.

Requirement 5.10: IF a new submission matches an existing organization,
Budget_Scope, AI_Product, Token_Budget, and Reporting_Period, THEN the
platform SHALL flag the new submission for duplicate review.

The service flags exact fingerprint matches for moderator review and
creates a DUPLICATE moderation case.  It never automatically rejects.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    ModerationCase,
    ModerationCaseState,
    ModerationSource,
    TokenBudgetRecord,
)
from token_budget_platform.domain.ports import Clock
from token_budget_platform.domain.submission.duplicate_detector import (
    DuplicateFingerprint,
    compute_fingerprint,
)


class DuplicateDetectionService:
    """Checks submissions for duplicates and flags matches for review.

    Uses the domain-level fingerprint computation and queries existing
    records to find matches.  Never auto-rejects — only sets the
    duplicate_flag and creates a moderation case for moderator review.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock

    def _now(self):
        if self._clock is not None:
            return self._clock.now()
        return timezone.now()

    def compute_and_check(
        self,
        *,
        organization_name: str,
        budget_scope: str,
        ai_product_id: str,
        token_budget: Decimal | int | str,
        reporting_start: date,
        reporting_end: date,
        exclude_record_id: UUID | None = None,
    ) -> tuple[bool, DuplicateFingerprint]:
        """Compute a fingerprint and check for existing matches.

        Args:
            organization_name: The submitted organization name.
            budget_scope: The budget scope (e.g., TEAM, INDIVIDUAL).
            ai_product_id: The AI product controlled-value ID.
            token_budget: The token budget amount.
            reporting_start: Reporting period start date.
            reporting_end: Reporting period end date.
            exclude_record_id: Optional record ID to exclude from the check
                              (used when amending an existing record).

        Returns:
            A tuple of (is_duplicate, fingerprint).
        """
        fingerprint = compute_fingerprint(
            organization_name=organization_name,
            budget_scope=budget_scope,
            ai_product_id=ai_product_id,
            token_budget=token_budget,
            reporting_start=reporting_start,
            reporting_end=reporting_end,
        )

        # Query existing records for fingerprint matches
        queryset = TokenBudgetRecord.objects.filter(
            duplicate_fingerprint=fingerprint.canonical_key,
        )
        if exclude_record_id is not None:
            queryset = queryset.exclude(id=exclude_record_id)

        is_duplicate = queryset.exists()
        return is_duplicate, fingerprint

    @transaction.atomic
    def flag_duplicate(
        self,
        record: TokenBudgetRecord,
        fingerprint: DuplicateFingerprint,
    ) -> None:
        """Flag a record as a duplicate and create a moderation case.

        Sets duplicate_flag=True, stores the fingerprint, and creates a
        DUPLICATE moderation case for moderator review.  Does NOT reject
        the submission.
        """
        record.duplicate_flag = True
        record.duplicate_fingerprint = fingerprint.canonical_key
        record.save(
            update_fields=["duplicate_flag", "duplicate_fingerprint", "updated_at", "row_version"]
        )

        # Create a moderation case for duplicate review
        ModerationCase.objects.create(
            record=record,
            source=ModerationSource.DUPLICATE,
            category="DUPLICATE_SUBMISSION",
            state=ModerationCaseState.OPEN,
            priority=30,  # Lower priority than reports/disputes
            opened_at=self._now(),
        )

    def store_fingerprint(
        self,
        record: TokenBudgetRecord,
        fingerprint: DuplicateFingerprint,
    ) -> None:
        """Store the fingerprint on a record without flagging it.

        Used for new records that are not duplicates so that future
        submissions can be checked against them.
        """
        record.duplicate_fingerprint = fingerprint.canonical_key
        record.save(update_fields=["duplicate_fingerprint", "updated_at", "row_version"])
