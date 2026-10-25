"""Django/PostgreSQL repository adapters.

Every public catalog method starts from ``public_queryset`` so rejected,
withdrawn, and synchronously suppressed records cannot leak through a read path.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from token_budget_platform.adapters.django.models import (
    ModerationCase,
    RecordVersion,
    TokenBudgetRecord,
)


class DjangoPublicRecordRepository:
    """Public record queries with eligibility injected at the boundary."""

    def eligible(self) -> Any:
        return TokenBudgetRecord.public_queryset().select_related(
            "organization", "model_provider", "ai_product", "workload_category", "region"
        )

    def for_organization(self, organization_id: UUID) -> Any:
        return self.eligible().filter(organization_id=organization_id)

    def search(self, *, organization: str = "", provider: str = "", product: str = "") -> Any:
        queryset = self.eligible()
        if organization:
            queryset = queryset.filter(organization_search__icontains=organization)
        if provider:
            queryset = queryset.filter(provider_search__icontains=provider)
        if product:
            queryset = queryset.filter(product_search__icontains=product)
        return queryset


class DjangoRecordVersionRepository:
    def for_record(self, record_id: UUID) -> Any:
        return RecordVersion.objects.filter(record_id=record_id).order_by("version", "id")


class DjangoModerationCaseRepository:
    def open_for_record(self, record_id: UUID) -> Any:
        return ModerationCase.objects.filter(record_id=record_id, state="OPEN").order_by(
            "-priority", "opened_at"
        )
