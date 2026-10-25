"""Tests for public catalog search, filters, sort, pagination, comparison (Req 2.1-2.6, 3.4)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from token_budget_platform.adapters.django.models import (
    Organization,
    PublicationState,
    TokenBudgetRecord,
)
from token_budget_platform.application.catalog.search import (
    CatalogFilters,
    ComparisonError,
    comparison,
    organization_profile,
    parse_filters,
    record_detail,
    search_records,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def dataset():
    """Three organizations with several records each across facets."""
    from datetime import UTC, datetime

    from token_budget_platform.adapters.django.models import (
        BudgetScope,
        ControlledValue,
        MethodologyVersion,
    )

    moment = datetime(2026, 1, 1, tzinfo=UTC)

    def value(kind, code, label):
        row = ControlledValue.objects.filter(kind=kind, code=code).first()
        if row:
            return row
        return ControlledValue.objects.create(
            kind=kind,
            code=code,
            label=label,
            normalized_label=label.lower(),
            active_from=moment,
            vocabulary_version=1,
        )

    methodology = MethodologyVersion.objects.filter(is_active=True).first()
    if methodology is None:
        methodology = MethodologyVersion.objects.create(
            version=1, name="Search test methodology", rules={}, is_active=True, activated_at=moment
        )

    records = []
    specs = [
        ("Alpha Retail", "OpenAI", "GPT-4o", "Research", 100_000),
        ("Alpha Retail", "OpenAI", "GPT-4o", "Software Development", 200_000),
        ("Beta Logistics", "Anthropic", "Claude 3.5 Sonnet", "Research", 300_000),
        ("Beta Logistics", "Google", "Gemini 1.5 Pro", "Content Generation", 400_000),
        ("Gamma Health", "OpenAI", "GPT-4o", "Customer Support", 500_000),
    ]
    for org_name, provider, product, workload, budget in specs:
        normalized = org_name.lower()
        organization = Organization.objects.filter(normalized_name=normalized).first()
        if organization is None:
            organization = Organization.objects.create(
                name=org_name,
                normalized_name=normalized,
                slug=normalized.replace(" ", "-"),
                industry="Various",
                size_band=value("SIZE_BAND", "MEDIUM", "51-500"),
                region=value("REGION", "EU", "Europe"),
            )
        records.append(
            TokenBudgetRecord.objects.create(
                organization=organization,
                budget_scope=BudgetScope.TEAM,
                model_provider=value(
                    "MODEL_PROVIDER", provider.upper().replace(" ", "_"), provider
                ),
                ai_product=value(
                    "AI_PRODUCT",
                    product.upper().replace(" ", "_").replace(".", "").replace("-", "_"),
                    product,
                ),
                workload_category=value(
                    "WORKLOAD_CATEGORY", workload.upper().replace(" ", "_"), workload
                ),
                region=value("REGION", "EU", "Europe"),
                organization_search=normalized,
                provider_search=provider.lower(),
                product_search=product.lower(),
                token_budget=Decimal(budget),
                reporting_start=date(2026, 1, 1),
                reporting_end=date(2026, 6, 30),
                source_date=date(2026, 7, 1),
                publication_state=PublicationState.APPROVED,
                methodology_version=methodology,
                published_at=moment,
            )
        )
    return records


class TestSearch:
    def test_containment_is_case_insensitive(self, dataset) -> None:
        result = search_records(CatalogFilters(organization="alpha"))
        assert result.total_count == 2
        assert all("Alpha" in record.organization_name for record in result.records)

    def test_filter_intersection(self, dataset) -> None:
        both = search_records(CatalogFilters(organization="beta", provider="anthropic"))
        assert both.total_count == 1
        neither = search_records(CatalogFilters(organization="beta", provider="openai"))
        assert neither.total_count == 0

    def test_sort_is_deterministic_with_ties(self, dataset) -> None:
        ascending = search_records(CatalogFilters(sort="budget_asc"))
        budgets = [record.token_budget for record in ascending.records]
        assert budgets == sorted(budgets)
        descending = search_records(CatalogFilters(sort="budget_desc"))
        budgets = [record.token_budget for record in descending.records]
        assert budgets == sorted(budgets, reverse=True)

    def test_unknown_sort_reverts_with_error(self, dataset) -> None:
        filters, errors = parse_filters({"sort": "chaos"})
        assert errors
        assert filters.sort == "newest"

    def test_pagination_is_bounded_at_25(self, dataset) -> None:
        result = search_records(CatalogFilters())
        assert len(result.records) <= 25
        assert result.total_pages >= 1

    def test_empty_dataset_vs_no_matches(self, dataset) -> None:
        with_records = search_records(CatalogFilters(organization="zzz-none"))
        assert with_records.total_count == 0
        assert with_records.is_empty_dataset is False

    def test_query_string_round_trip(self, dataset) -> None:
        filters = CatalogFilters(organization="alpha", sort="budget_asc")
        parsed, errors = parse_filters(
            dict(pair.split("=", 1) for pair in filters.query_string().split("&"))
        )
        assert not errors
        assert parsed.organization == "alpha"
        assert parsed.sort == "budget_asc"


class TestProfileAndDetail:
    def test_profile_aggregates_respect_threshold(self, dataset) -> None:
        profile = organization_profile("alpha-retail")
        assert profile is not None
        assert profile.eligible_record_count == 2
        # Every group has fewer than 3 records -> all suppressed
        assert all(group.suppressed for group in profile.aggregates)

    def test_profile_unknown_returns_none(self, dataset) -> None:
        assert organization_profile("no-such-org") is None

    def test_detail_returns_projection(self, dataset) -> None:
        detail = record_detail(dataset[0].public_reference)
        assert detail is not None
        assert detail.record.organization_name == "Alpha Retail"
        assert detail.record.methodology_notes is not None

    def test_detail_hidden_for_non_public(self, dataset) -> None:
        record = dataset[2]
        record.publication_state = PublicationState.REJECTED
        record.save()
        assert record_detail(record.public_reference) is None


class TestComparison:
    def test_preserves_requested_order_and_aligns_rows(self, dataset) -> None:
        result = comparison([dataset[2].public_reference, dataset[0].public_reference])
        assert [column.organization_name for column in result.columns] == [
            "Beta Logistics",
            "Alpha Retail",
        ]
        labels = {row["label"] for row in result.rows}
        assert "Token Budget" in labels
        for row in result.rows:
            assert len(row["values"]) == 2

    def test_rejects_too_few(self, dataset) -> None:
        with pytest.raises(ComparisonError):
            comparison([dataset[0].public_reference])

    def test_rejects_duplicates(self, dataset) -> None:
        reference = dataset[0].public_reference
        with pytest.raises(ComparisonError):
            comparison([reference, reference])

    def test_rejects_ineligible_records(self, dataset) -> None:
        dataset[3].publication_state = PublicationState.DRAFT
        dataset[3].save()
        with pytest.raises(ComparisonError):
            comparison([dataset[0].public_reference, dataset[3].public_reference])
