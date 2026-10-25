"""Public catalog query service.

Requirements: 1.2, 1.3, 1.4, 1.5, 1.6
- Landing: published count and most recent update date.
- Browse: paginated public records with fixed headings and empty-state.
- All queries enforce the public eligibility predicate.
- All results use PublicRecordDTO; no private model reaches callers.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

from token_budget_platform.domain.catalog.public_projection import (
    BROWSE_HEADINGS,
    SCOPE_EXPLANATION,
    VERIFICATION_STATUS_DEFINITIONS,
    PublicProjectionBuilder,
    PublicRecordDTO,
)

PAGE_SIZE = 25


@dataclasses.dataclass(frozen=True, slots=True)
class DatasetSummary:
    """Landing page dataset summary (Req 1.2)."""

    published_count: int
    last_updated: datetime | None
    scope_explanation: str


@dataclasses.dataclass(frozen=True, slots=True)
class BrowsePage:
    """A page of public records for the browse view (Req 1.3–1.6)."""

    records: list[PublicRecordDTO]
    total_count: int
    page_number: int
    total_pages: int
    headings: list[str]
    has_next: bool
    has_previous: bool
    is_empty: bool
    scope_explanation: str
    status_definitions: dict[str, str]


class PublicRecordQuery:
    """Application query service for public catalog reads.

    Wraps the ORM through the public eligibility predicate and transforms
    results exclusively through PublicProjectionBuilder.
    """

    def get_dataset_summary(self) -> DatasetSummary:
        """Return the count of published records and most recent update date."""
        from token_budget_platform.adapters.django.models import TokenBudgetRecord

        qs = TokenBudgetRecord.public_queryset()
        count = qs.count()
        last_updated: datetime | None = None
        if count > 0:
            latest = qs.order_by("-published_at").values_list("published_at", flat=True).first()
            last_updated = latest

        return DatasetSummary(
            published_count=count,
            last_updated=last_updated,
            scope_explanation=SCOPE_EXPLANATION,
        )

    def browse(
        self,
        *,
        page: int = 1,
        observation_date: date | None = None,
    ) -> BrowsePage:
        """Return a paginated page of publicly eligible records.

        Pages contain at most PAGE_SIZE (25) records. Fixed headings are
        always included regardless of record count.
        """
        from token_budget_platform.adapters.django.models import TokenBudgetRecord

        obs = observation_date or date.today()

        qs = (
            TokenBudgetRecord.public_queryset()
            .select_related(
                "organization",
                "organization__size_band",
                "organization__region",
                "model_provider",
                "ai_product",
                "workload_category",
                "region",
            )
            .order_by("-published_at", "id")
        )

        total_count = qs.count()
        total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)

        # Clamp page to valid range
        page = max(1, min(page, total_pages))

        offset = (page - 1) * PAGE_SIZE
        page_qs = qs[offset : offset + PAGE_SIZE]

        records = [
            PublicProjectionBuilder.from_record(record, observation_date=obs) for record in page_qs
        ]

        return BrowsePage(
            records=records,
            total_count=total_count,
            page_number=page,
            total_pages=total_pages,
            headings=BROWSE_HEADINGS,
            has_next=page < total_pages,
            has_previous=page > 1,
            is_empty=total_count == 0,
            scope_explanation=SCOPE_EXPLANATION,
            status_definitions=VERIFICATION_STATUS_DEFINITIONS,
        )
