"""Domain value objects for normalization results.

Normalized metrics are exact Decimal calculations with explicit formula
identifiers, source values, and methodology version tracking.  Missing
inputs are represented by a calculation result, never by a misleading
database row or silently imputed value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID


class MetricType(StrEnum):
    """Enumeration of normalized metric calculations."""

    BUDGET_30D = "budget_30d"
    TOKENS_PER_SEAT_30D = "tokens_per_seat_30d"
    TOKENS_PER_EXECUTION = "tokens_per_execution"
    USAGE_30D = "usage_30d"
    USAGE_PER_SEAT_30D = "usage_per_seat_30d"
    USAGE_PER_EXECUTION = "usage_per_execution"
    UNAVAILABLE = "unavailable"


class FormulaCode(StrEnum):
    """Stable identifiers for each normalization formula."""

    BUDGET_30D = "budget_30d_v1"
    TOKENS_PER_SEAT_30D = "tokens_per_seat_30d_v1"
    TOKENS_PER_EXECUTION = "tokens_per_execution_v1"
    USAGE_30D = "usage_30d_v1"
    USAGE_PER_SEAT_30D = "usage_per_seat_30d_v1"
    USAGE_PER_EXECUTION = "usage_per_execution_v1"


# 18-digit precision, half-even rounding for display
NORMALIZATION_PRECISION = 18
DISPLAY_SCALE = 2


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """One computed or unavailable normalized metric.

    For successful calculations: value is set, missing_inputs is empty.
    For unavailable metrics: value is None, missing_inputs lists what's needed.
    """

    metric_type: MetricType
    value: Decimal | None
    formula_code: str
    methodology_version: int
    source_values: dict[str, Any] = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)
    display_scale: int = DISPLAY_SCALE

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class NormalizationInput:
    """Structured inputs extracted from a record for normalization."""

    record_id: UUID
    budget_scope: str
    token_budget: Decimal
    reporting_start: Any  # date
    reporting_end: Any  # date
    seat_count: int | None = None
    workload_count: int | None = None
    token_usage: Decimal | None = None
    methodology_version: int = 1
