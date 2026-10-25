"""Domain value objects and services for privacy-threshold aggregation and calendar staleness.

Requirements 4.3, 4.4, 4.5, 9.9:
- 4.3: WHEN at least three published records share an org and comparison dimension,
        display median/min/max/count.
- 4.4: IF fewer than three records share, suppress aggregates and display
        minimum-group-size explanation.
- 4.5: WHEN a record crosses 12 calendar months after Reporting_Period end date,
        automatically label as stale without Moderator action.
- 9.9: Disclose aggregation dimensions, contributing record count, and
        minimum-group-size rule.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

MINIMUM_GROUP_SIZE = 3
STALENESS_MONTHS = 12


class AggregationDimension(StrEnum):
    """Documented comparison dimensions used for grouping."""

    ORGANIZATION = "organization"
    BUDGET_SCOPE = "budget_scope"
    MODEL_PROVIDER = "model_provider"
    AI_PRODUCT = "ai_product"
    WORKLOAD_CATEGORY = "workload_category"
    REGION = "region"


@dataclass(frozen=True, slots=True)
class AggregateGroup:
    """A single aggregate group result (above or below threshold)."""

    dimension_key: str
    dimension_values: dict[str, str]
    count: int
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    median: Decimal | None = None
    suppressed: bool = False
    suppression_reason: str = ""

    @property
    def meets_threshold(self) -> bool:
        return self.count >= MINIMUM_GROUP_SIZE


@dataclass(frozen=True, slots=True)
class AggregateDisclosure:
    """Transparency metadata for aggregate statistics (Requirement 9.9)."""

    dimensions: list[str]
    contributing_record_count: int
    minimum_group_size: int = MINIMUM_GROUP_SIZE
    rule_explanation: str = (
        f"Aggregate statistics are displayed only when at least "
        f"{MINIMUM_GROUP_SIZE} published records share the grouping dimensions. "
        f"Groups below this threshold are suppressed to protect contributor privacy."
    )


@dataclass(frozen=True, slots=True)
class EligibleRecord:
    """Minimal record data needed for aggregation calculations."""

    record_id: UUID
    organization_id: UUID
    budget_scope: str
    model_provider_id: UUID
    ai_product_id: UUID
    workload_category_id: UUID
    region_id: UUID
    token_budget: Decimal
    reporting_end: date


@dataclass(frozen=True, slots=True)
class StalenessResult:
    """Result of a staleness check for a single record."""

    record_id: UUID
    reporting_end: date
    is_stale: bool
    boundary_date: date
    months_elapsed: int


# ---------------------------------------------------------------------------
# Calendar staleness calculation
# ---------------------------------------------------------------------------


def add_calendar_months(base: date, months: int) -> date:
    """Add ``months`` calendar months to ``base`` with end-of-month clamping.

    The documented 12-calendar-month boundary uses month clamping:
    - Adding 12 months to Jan 31 yields Jan 31 of the following year.
    - Adding 12 months to Feb 29 (leap) yields Feb 28 (non-leap) or Feb 29 (leap).
    - The day is clamped to the last day of the target month if it exceeds the
      target month's length.
    """
    total_months = base.month - 1 + months
    target_year = base.year + total_months // 12
    target_month = total_months % 12 + 1
    max_day = calendar.monthrange(target_year, target_month)[1]
    target_day = min(base.day, max_day)
    return date(target_year, target_month, target_day)


def compute_staleness_boundary(reporting_end: date) -> date:
    """Return the date at which a record becomes stale (exclusive boundary).

    A record is stale when the observation date is strictly later than the
    reporting_end plus STALENESS_MONTHS calendar months under month clamping.
    """
    return add_calendar_months(reporting_end, STALENESS_MONTHS)


def is_stale(reporting_end: date, observation_date: date) -> bool:
    """Determine whether a record is stale at the given observation date.

    A record crosses 12 calendar months after the Reporting_Period end date
    when the observation date is strictly later than the boundary date
    (the end date plus 12 calendar months with month clamping).
    """
    boundary = compute_staleness_boundary(reporting_end)
    return observation_date > boundary


def months_between(start: date, end: date) -> int:
    """Approximate month difference used for informational display only."""
    return (end.year - start.year) * 12 + (end.month - start.month)


def check_staleness(
    record_id: UUID, reporting_end: date, observation_date: date
) -> StalenessResult:
    """Check staleness for a single record and return full result."""
    boundary = compute_staleness_boundary(reporting_end)
    stale = observation_date > boundary
    elapsed = months_between(reporting_end, observation_date)
    return StalenessResult(
        record_id=record_id,
        reporting_end=reporting_end,
        is_stale=stale,
        boundary_date=boundary,
        months_elapsed=elapsed,
    )


# ---------------------------------------------------------------------------
# Privacy-threshold aggregate computation
# ---------------------------------------------------------------------------


def _sorted_median(values: list[Decimal]) -> Decimal:
    """Compute the median of a pre-sorted non-empty list of Decimal values.

    For even-length lists, returns the average of the two middle values
    using Decimal arithmetic (no floating point).
    """
    n = len(values)
    if n == 0:
        raise ValueError("cannot compute median of empty sequence")
    mid = n // 2
    if n % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) / Decimal(2)


def compute_aggregate(
    dimension_key: str,
    dimension_values: dict[str, str],
    token_budgets: Sequence[Decimal],
) -> AggregateGroup:
    """Compute an aggregate group respecting the privacy threshold.

    If fewer than MINIMUM_GROUP_SIZE records exist, returns a suppressed
    group with no statistics (Requirement 4.4).

    When the threshold is met, returns count/min/max/median (Requirement 4.3).
    """
    count = len(token_budgets)
    if count < MINIMUM_GROUP_SIZE:
        return AggregateGroup(
            dimension_key=dimension_key,
            dimension_values=dimension_values,
            count=count,
            suppressed=True,
            suppression_reason=(
                f"Aggregate statistics require at least {MINIMUM_GROUP_SIZE} "
                f"published records sharing the grouping dimensions. "
                f"This group has {count} record{'s' if count != 1 else ''}."
            ),
        )

    sorted_values = sorted(token_budgets)
    return AggregateGroup(
        dimension_key=dimension_key,
        dimension_values=dimension_values,
        count=count,
        minimum=sorted_values[0],
        maximum=sorted_values[-1],
        median=_sorted_median(sorted_values),
        suppressed=False,
    )


def build_disclosure(
    dimensions: list[str],
    contributing_record_count: int,
) -> AggregateDisclosure:
    """Build the transparency disclosure for aggregate statistics (Requirement 9.9).

    Discloses the aggregation dimensions, contributing record count,
    and minimum-group-size rule.
    """
    return AggregateDisclosure(
        dimensions=dimensions,
        contributing_record_count=contributing_record_count,
    )
