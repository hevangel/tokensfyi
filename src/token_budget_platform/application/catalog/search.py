"""Public catalog search, organization profiles, and comparison queries.

Requirements:
- 2.1 search equals case-insensitive containment on organization/provider/product.
- 2.2 filters equal predicate intersection over documented dimensions.
- 2.3 sorts are stable and monotonic (unique id tiebreaker).
- 2.4 shareable URL state encoded canonically in the query string; unknown
  values are rejected with safe errors instead of being silently dropped.
- 2.5 pages are bounded (25) and carry the matching record count.
- 2.6 empty states distinguish "no records at all" from "no matches for the
  active criteria" and offer a clear-criteria link.
- 3.3 organization profiles show canonical metadata, aggregates with
  disclosure, and eligible records; record detail shows provenance and
  correction/stale/disputed indicators.
- 3.4 comparison accepts two through five distinct eligible records, preserves
  the requested order, and aligns every required field.

Every result passes through PublicProjectionBuilder; no private model data
reaches callers (Req 1.3, 9.3).
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet

from token_budget_platform.adapters.django.models import (
    BudgetScope,
    ControlledValue,
    ModerationCase,
    ModerationCaseState,
    NormalizedMetric,
    Organization,
    TokenBudgetRecord,
    VerificationStatus,
)
from token_budget_platform.application.catalog.queries import PAGE_SIZE
from token_budget_platform.domain.catalog.public_projection import (
    SCOPE_EXPLANATION,
    VERIFICATION_STATUS_DEFINITIONS,
    PublicProjectionBuilder,
    PublicRecordDTO,
)
from token_budget_platform.domain.governance.aggregation import (
    AggregateDisclosure,
    AggregateGroup,
    build_disclosure,
    compute_aggregate,
)

# --- Sort allowlist (Req 2.3) ---

SORT_OPTIONS: dict[str, tuple[str, ...]] = {
    "newest": ("-published_at", "id"),
    "budget_desc": ("-token_budget", "id"),
    "budget_asc": ("token_budget", "id"),
    "period_end_desc": ("-reporting_end", "id"),
    "organization": ("organization_search", "id"),
}
DEFAULT_SORT = "newest"

FILTER_FIELDS = (
    "organization",
    "provider",
    "product",
    "workload",
    "region",
    "budget_scope",
    "verification_status",
)


# --- Filter value object and query-string codec (Req 2.4) ---


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogFilters:
    """Validated catalog filter state; serializable to a canonical query string."""

    organization: str = ""
    provider: str = ""
    product: str = ""
    workload: str = ""
    region: str = ""
    budget_scope: str = ""
    verification_status: str = ""
    sort: str = DEFAULT_SORT

    def active(self) -> dict[str, str]:
        """Return only the non-default criteria, in canonical field order."""
        values: dict[str, str] = {}
        for name in FILTER_FIELDS:
            value = getattr(self, name)
            if value:
                values[name] = value
        if self.sort != DEFAULT_SORT:
            values["sort"] = self.sort
        return values

    def query_string(self, *, page: int | None = None) -> str:
        """Encode the state canonically; page 1 is omitted."""
        params = self.active()
        if page is not None and page > 1:
            params["page"] = str(page)
        if not params:
            return ""
        return "&".join(f"{key}={_encode(value)}" for key, value in params.items())

    def is_default(self) -> bool:
        return not self.active()


def _encode(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value)


def parse_filters(get_params: Any) -> tuple[CatalogFilters, list[str]]:
    """Parse and validate filter state from a request's GET parameters.

    Returns the filters plus a list of safe validation error messages for
    rejected values.  Unknown sort keys revert to the default with an error;
    invalid enum filters are reported so users can correct the shared URL.
    """

    def _text(name: str) -> str:
        raw = get_params.get(name, "")
        return str(raw).strip()[:200] if raw else ""

    errors: list[str] = []

    sort = _text("sort") or DEFAULT_SORT
    if sort not in SORT_OPTIONS:
        errors.append(f"Unknown sort '{sort}'. Choose one of: {', '.join(SORT_OPTIONS)}.")
        sort = DEFAULT_SORT

    budget_scope = _text("budget_scope")
    if budget_scope and budget_scope not in BudgetScope.values:
        errors.append("Unknown budget scope filter.")
        budget_scope = ""

    verification_status = _text("verification_status")
    if verification_status and verification_status not in VerificationStatus.values:
        errors.append("Unknown verification status filter.")
        verification_status = ""

    filters = CatalogFilters(
        organization=_text("organization"),
        provider=_text("provider"),
        product=_text("product"),
        workload=_text("workload"),
        region=_text("region"),
        budget_scope=budget_scope,
        verification_status=verification_status,
        sort=sort,
    )
    return filters, errors


# --- Search query (Req 2.1, 2.2, 2.3, 2.5, 2.6) ---


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogPage:
    """One page of catalog results with filter state and counts."""

    records: list[PublicRecordDTO]
    total_count: int
    page_number: int
    total_pages: int
    has_next: bool
    has_previous: bool
    filters: CatalogFilters
    filter_errors: list[str]
    is_empty_dataset: bool
    headings: list[str]
    scope_explanation: str
    status_definitions: dict[str, str]

    @property
    def is_filtered(self) -> bool:
        return not self.filters.is_default()

    @property
    def clear_url(self) -> str:
        return "/browse/"


def _eligible_queryset() -> QuerySet[TokenBudgetRecord]:
    return TokenBudgetRecord.public_queryset().select_related(
        "organization",
        "organization__size_band",
        "organization__region",
        "model_provider",
        "ai_product",
        "workload_category",
        "region",
    )


def _apply_filters(
    qs: QuerySet[TokenBudgetRecord], filters: CatalogFilters
) -> QuerySet[TokenBudgetRecord]:
    """Intersection of predicates; containment searches are case-insensitive."""
    if filters.organization:
        qs = qs.filter(organization_search__icontains=filters.organization)
    if filters.provider:
        qs = qs.filter(provider_search__icontains=filters.provider)
    if filters.product:
        qs = qs.filter(product_search__icontains=filters.product)
    if filters.workload:
        qs = qs.filter(
            Q(workload_category__label__iexact=filters.workload)
            | Q(workload_category__code__iexact=filters.workload)
        )
    if filters.region:
        qs = qs.filter(
            Q(region__label__iexact=filters.region) | Q(region__code__iexact=filters.region)
        )
    if filters.budget_scope:
        qs = qs.filter(budget_scope=filters.budget_scope)
    if filters.verification_status:
        qs = qs.filter(verification_status=filters.verification_status)
    return qs


def search_records(
    filters: CatalogFilters,
    *,
    page: int = 1,
    observation_date: date | None = None,
) -> CatalogPage:
    """Return one bounded page of filtered, sorted, publicly eligible records."""
    obs = observation_date or date.today()

    total_count = _apply_filters(_eligible_queryset(), filters).count()
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    queryset = _apply_filters(_eligible_queryset(), filters)
    order_by = SORT_OPTIONS.get(filters.sort, SORT_OPTIONS[DEFAULT_SORT])
    offset = (page - 1) * PAGE_SIZE
    page_records = list(queryset.order_by(*order_by)[offset : offset + PAGE_SIZE])

    is_empty_dataset = TokenBudgetRecord.public_queryset().count() == 0

    return CatalogPage(
        records=[
            PublicProjectionBuilder.from_record(record, observation_date=obs)
            for record in page_records
        ],
        total_count=total_count,
        page_number=page,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_previous=page > 1,
        filters=filters,
        filter_errors=[],
        is_empty_dataset=is_empty_dataset,
        headings=[
            "Organization",
            "Model Provider",
            "AI Product",
            "Workload Category",
            "Budget Scope",
            "Token Budget",
            "Reporting Period",
            "Geographic Region",
            "Verification Status",
        ],
        scope_explanation=SCOPE_EXPLANATION,
        status_definitions=VERIFICATION_STATUS_DEFINITIONS,
    )


# --- Organization profile and record detail (Req 4.1-4.5, 9.9, 12.5, 12.6) ---


@dataclasses.dataclass(frozen=True, slots=True)
class OrganizationProfile:
    """Canonical public organization profile with disclosed aggregates."""

    organization_id: UUID
    slug: str
    name: str
    industry: str
    size_band: str
    region: str
    eligible_record_count: int
    aggregates: list[AggregateGroup]
    disclosure: AggregateDisclosure
    records: list[PublicRecordDTO]
    merged_from: list[str]
    observation_date: date


def organization_profile(
    slug: str, *, observation_date: date | None = None
) -> OrganizationProfile | None:
    """Return the public profile for one organization, or None if unknown.

    Organizations merged into this one are disclosed by name so historical
    references remain meaningful (Req 12.6) without exposing any private
    history (Req 9.3).
    """
    obs = observation_date or date.today()
    organization = (
        Organization.objects.filter(slug=slug, is_active=True)
        .select_related("size_band", "region")
        .first()
    )
    if organization is None:
        return None

    eligible = list(
        _eligible_queryset().filter(organization=organization).order_by("-reporting_end", "id")
    )

    aggregates: list[AggregateGroup] = []
    dimensions: tuple[tuple[str, Any, Any], ...] = (
        ("budget_scope", lambda r: r.budget_scope, lambda r: r.budget_scope),
        ("model_provider", lambda r: str(r.model_provider_id), lambda r: r.model_provider.label),
        ("ai_product", lambda r: str(r.ai_product_id), lambda r: r.ai_product.label),
        (
            "workload_category",
            lambda r: str(r.workload_category_id),
            lambda r: r.workload_category.label,
        ),
        ("region", lambda r: str(r.region_id), lambda r: r.region.label),
    )
    for key, group_by, describe in dimensions:
        groups: dict[str, list[Decimal]] = {}
        labels: dict[str, str] = {}
        for record in eligible:
            group_key = group_by(record)
            groups.setdefault(group_key, []).append(record.token_budget)
            labels[group_key] = describe(record)
        for group_key, budgets in groups.items():
            aggregates.append(
                compute_aggregate(
                    dimension_key=key,
                    dimension_values={"value": labels[group_key]},
                    token_budgets=budgets,
                )
            )
    aggregates.sort(key=lambda g: (g.dimension_key, g.dimension_values["value"]))

    merged_names = list(
        Organization.objects.filter(merge_target=organization).values_list("name", flat=True)
    )

    return OrganizationProfile(
        organization_id=organization.id,
        slug=organization.slug,
        name=organization.name,
        industry=organization.industry,
        size_band=organization.size_band.label,
        region=organization.region.label,
        eligible_record_count=len(eligible),
        aggregates=aggregates,
        disclosure=build_disclosure(
            dimensions=[key for key, _, _ in dimensions],
            contributing_record_count=len(eligible),
        ),
        records=[
            PublicProjectionBuilder.from_record(record, observation_date=obs) for record in eligible
        ],
        merged_from=merged_names,
        observation_date=obs,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class RecordDetail:
    """Public record detail with indicators and normalized metric disclosure."""

    record: PublicRecordDTO
    organization_slug: str
    disputed: bool
    metrics: list[dict[str, str]]
    correction_date: date | None


def record_detail(
    public_reference: UUID, *, observation_date: date | None = None
) -> RecordDetail | None:
    """Return one publicly eligible record by its public reference."""
    record = _eligible_queryset().filter(public_reference=public_reference).first()
    if record is None:
        return None

    obs = observation_date or date.today()
    disputed = ModerationCase.objects.filter(
        record=record,
        state=ModerationCaseState.OPEN,
        source__in=("REPORT", "DISPUTE"),
    ).exists()

    metrics = [
        {
            "metric_type": metric.metric_type,
            "value": str(metric.value),
            "methodology": metric.methodology_version.name,
            "formula_code": metric.formula_code,
            "source_values": ", ".join(
                f"{key}={value}" for key, value in sorted(metric.source_values.items())
            )
            or "",
        }
        for metric in NormalizedMetric.objects.filter(record=record)
        .select_related("methodology_version")
        .order_by("metric_type")
    ]

    return RecordDetail(
        record=PublicProjectionBuilder.from_record(record, observation_date=obs),
        organization_slug=record.organization.slug,
        disputed=disputed or record.verification_status == VerificationStatus.DISPUTED,
        metrics=metrics,
        correction_date=record.correction_date,
    )


# --- Comparison (Req 3.1-3.6) ---


@dataclasses.dataclass(frozen=True, slots=True)
class ComparisonError(Exception):
    """Safe comparison selection error carrying a user-facing message."""

    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Aligned comparison of two through five eligible records.

    ``columns`` preserves the requested order.  ``rows`` holds one entry per
    aligned field with a value per column ("Not provided" when absent), so the
    semantic table is the authoritative representation (Req 11.4).
    """

    columns: list[PublicRecordDTO]
    rows: list[dict[str, Any]]
    metric_rows: list[dict[str, Any]]


_ComparisonAccessor = Any

_COMPARISON_FIELDS: tuple[tuple[str, _ComparisonAccessor], ...] = (
    ("Organization", lambda r: r.organization_name),
    ("Model Provider", lambda r: r.model_provider),
    ("AI Product", lambda r: r.ai_product),
    ("Workload Category", lambda r: r.workload_category),
    ("Budget Scope", lambda r: r.budget_scope),
    ("Token Budget", lambda r: str(r.token_budget)),
    (
        "Input Token Allocation",
        lambda r: str(r.input_tokens) if r.input_tokens is not None else "Not provided",
    ),
    (
        "Output Token Allocation",
        lambda r: str(r.output_tokens) if r.output_tokens is not None else "Not provided",
    ),
    (
        "Reporting Period",
        lambda r: f"{r.reporting_start.isoformat()} to {r.reporting_end.isoformat()}",
    ),
    ("Reporting Timezone", lambda r: r.reporting_timezone),
    ("Token Usage", lambda r: str(r.token_usage) if r.token_usage is not None else "Not provided"),
    (
        "Cost",
        lambda r: (
            f"{r.cost_minor_units} minor units ({r.cost_currency}, {r.cost_basis})"
            if r.cost_minor_units is not None
            else "Not provided"
        ),
    ),
    ("Source Date", lambda r: r.source_date.isoformat()),
    ("Verification Status", lambda r: r.verification_status),
    (
        "Last Reviewed",
        lambda r: r.last_reviewed_date.date().isoformat()
        if r.last_reviewed_date
        else "Not reviewed",
    ),
    ("Correction Date", lambda r: r.correction_date.isoformat() if r.correction_date else "None"),
    (
        "Staleness",
        lambda r: "Stale (more than 12 months past the reporting period)"
        if r.is_stale
        else "Current",
    ),
    ("Methodology Notes", lambda r: r.methodology_notes or "None provided"),
)


def comparison(
    public_references: list[UUID], *, observation_date: date | None = None
) -> ComparisonResult:
    """Compare two through five distinct publicly eligible records, in order.

    Raises ComparisonError with a safe message for wrong counts, duplicates,
    unknown references, or records that are not publicly eligible.
    """
    if not (2 <= len(public_references) <= 5):
        raise ComparisonError("Select between two and five records to compare.")

    if len(set(public_references)) != len(public_references):
        raise ComparisonError("Comparison selections must be distinct records.")

    columns: list[PublicRecordDTO] = []
    for reference in public_references:
        detail = record_detail(reference, observation_date=observation_date)
        if detail is None:
            raise ComparisonError("One or more selected records are not publicly available.")
        columns.append(detail.record)

    rows: list[dict[str, Any]] = []
    for label, accessor in _COMPARISON_FIELDS:
        rows.append({"label": label, "values": [str(accessor(column)) for column in columns]})

    metric_rows = _aligned_metric_rows(public_references)
    return ComparisonResult(columns=columns, rows=rows, metric_rows=metric_rows)


def _aligned_metric_rows(public_references: list[UUID]) -> list[dict[str, Any]]:
    """Align normalized metrics across the compared records by metric type."""
    by_type: dict[str, dict[UUID, str]] = {}
    for reference in public_references:
        for metric in (
            NormalizedMetric.objects.filter(record__public_reference=reference)
            .select_related("methodology_version")
            .order_by("metric_type")
        ):
            row = by_type.setdefault(metric.metric_type, {})
            row[reference] = str(metric.value)
    return [
        {
            "label": metric_type,
            "values": [
                by_type[metric_type].get(reference, "Not provided")
                for reference in public_references
            ],
        }
        for metric_type in sorted(by_type)
    ]


def parse_comparison_references(raw_value: str) -> list[UUID]:
    """Parse a comma-separated comparison URL parameter.

    Raises ComparisonError for malformed identifiers so the view can return a
    safe error instead of leaking resolution internals.
    """
    parts = [part.strip() for part in raw_value.split(",") if part.strip()]
    if not parts:
        raise ComparisonError("Provide the records to compare as comma-separated identifiers.")
    references: list[UUID] = []
    for part in parts:
        try:
            references.append(UUID(part))
        except ValueError:
            raise ComparisonError(
                "Comparison identifiers must be valid record references."
            ) from None
    return references


def active_controlled_value_options() -> dict[str, list[dict[str, str]]]:
    """Active controlled values grouped for forms, keyed by lowercase kind.

    Each option carries the controlled-value id (for submission payloads) and
    its label (for display and label-based filters).
    """
    values = ControlledValue.objects.filter(
        active_to__isnull=True,
    ).order_by("kind", "label")
    options: dict[str, list[dict[str, str]]] = {}
    for value in values:
        options.setdefault(value.kind.lower(), []).append(
            {"id": str(value.id), "label": value.label, "value": value.label}
        )
    return options
