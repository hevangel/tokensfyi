"""Normalization service: exact Decimal calculations with formula tracking.

Requirements 3.3, 3.4, 3.5, 12.7:
- 3.3: Display normalized metrics with calculation formula and source values.
- 3.4: Label metrics unavailable and identify each missing input.
- 3.5: Present both original values and 30-day normalized values.
- 12.7: Use one documented calculation rule for all records with the same scope.

Design contract: NormalizationService returns value, formula identifier/version,
source values, and missing inputs.  It never silently imputes counts or dates.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from typing import Any

from token_budget_platform.domain.governance.normalization import (
    DISPLAY_SCALE,
    NORMALIZATION_PRECISION,
    FormulaCode,
    MetricType,
    NormalizationInput,
    NormalizationResult,
)

# Ensure module-level context uses sufficient precision for intermediate calculations
getcontext().prec = 38


def _days_inclusive(start: date, end: date) -> Decimal:
    """Inclusive day count: same-day periods equal one day."""
    return Decimal((end - start).days + 1)


def _decimal(value: int | Decimal | None) -> Decimal | None:
    """Coerce to Decimal without floating point."""
    if value is None:
        return None
    return Decimal(value)


class NormalizationService:
    """Computes exact normalized metrics for a single record.

    All arithmetic uses Python's Decimal with explicit 18-digit precision
    and half-even display rounding.  No binary floating point is used.
    """

    def normalize(self, inp: NormalizationInput) -> list[NormalizationResult]:
        """Compute all applicable normalized metrics for the given input.

        Returns a list of NormalizationResult objects.  Metrics that cannot
        be computed (missing inputs) are included with value=None and the
        missing_inputs list populated.  No row should be persisted for those.
        """
        results: list[NormalizationResult] = []
        methodology_version = inp.methodology_version

        # Validate dates exist and are coherent
        if inp.reporting_start is None or inp.reporting_end is None:
            # Cannot compute anything without dates
            results.append(
                self._unavailable(
                    MetricType.BUDGET_30D,
                    methodology_version,
                    missing=["reporting_start", "reporting_end"],
                    sources={},
                )
            )
            return results

        days = _days_inclusive(inp.reporting_start, inp.reporting_end)
        token_budget = _decimal(inp.token_budget)

        if token_budget is None or days <= 0:
            results.append(
                self._unavailable(
                    MetricType.BUDGET_30D,
                    methodology_version,
                    missing=["token_budget"] if token_budget is None else ["valid_period"],
                    sources={},
                )
            )
            return results

        thirty = Decimal(30)

        # --- budget_30d: always computed when dates and budget exist ---
        budget_30d = self._precise_divide(token_budget * thirty, days)
        results.append(
            NormalizationResult(
                metric_type=MetricType.BUDGET_30D,
                value=budget_30d,
                formula_code=FormulaCode.BUDGET_30D,
                methodology_version=methodology_version,
                source_values={
                    "token_budget": str(token_budget),
                    "days": str(days),
                },
                display_scale=DISPLAY_SCALE,
            )
        )

        # --- tokens_per_seat_30d: individual/team/organization with seat_count ---
        seat_scopes = ("INDIVIDUAL", "TEAM", "ORGANIZATION")
        if inp.budget_scope in seat_scopes:
            if inp.seat_count is not None and inp.seat_count >= 1:
                seat_count = Decimal(inp.seat_count)
                per_seat_30d = self._precise_divide(token_budget * thirty, seat_count * days)
                results.append(
                    NormalizationResult(
                        metric_type=MetricType.TOKENS_PER_SEAT_30D,
                        value=per_seat_30d,
                        formula_code=FormulaCode.TOKENS_PER_SEAT_30D,
                        methodology_version=methodology_version,
                        source_values={
                            "token_budget": str(token_budget),
                            "seat_count": str(inp.seat_count),
                            "days": str(days),
                        },
                        display_scale=DISPLAY_SCALE,
                    )
                )
            else:
                results.append(
                    self._unavailable(
                        MetricType.TOKENS_PER_SEAT_30D,
                        methodology_version,
                        missing=["seat_count"],
                        sources={"token_budget": str(token_budget), "days": str(days)},
                    )
                )

        # --- tokens_per_execution: workload scope with workload_count ---
        if inp.budget_scope == "WORKLOAD":
            if inp.workload_count is not None and inp.workload_count >= 1:
                workload_count = Decimal(inp.workload_count)
                per_execution = self._precise_divide(token_budget, workload_count)
                results.append(
                    NormalizationResult(
                        metric_type=MetricType.TOKENS_PER_EXECUTION,
                        value=per_execution,
                        formula_code=FormulaCode.TOKENS_PER_EXECUTION,
                        methodology_version=methodology_version,
                        source_values={
                            "token_budget": str(token_budget),
                            "workload_count": str(inp.workload_count),
                        },
                        display_scale=DISPLAY_SCALE,
                    )
                )
            else:
                results.append(
                    self._unavailable(
                        MetricType.TOKENS_PER_EXECUTION,
                        methodology_version,
                        missing=["workload_count"],
                        sources={"token_budget": str(token_budget)},
                    )
                )

        # --- Usage metrics: only when usage exists ---
        token_usage = _decimal(inp.token_usage)
        if token_usage is not None:
            # usage_30d
            usage_30d = self._precise_divide(token_usage * thirty, days)
            results.append(
                NormalizationResult(
                    metric_type=MetricType.USAGE_30D,
                    value=usage_30d,
                    formula_code=FormulaCode.USAGE_30D,
                    methodology_version=methodology_version,
                    source_values={
                        "token_usage": str(token_usage),
                        "days": str(days),
                    },
                    display_scale=DISPLAY_SCALE,
                )
            )

            # usage_per_seat_30d: seat scopes with seat_count
            if inp.budget_scope in seat_scopes:
                if inp.seat_count is not None and inp.seat_count >= 1:
                    seat_count = Decimal(inp.seat_count)
                    usage_per_seat_30d = self._precise_divide(
                        token_usage * thirty, seat_count * days
                    )
                    results.append(
                        NormalizationResult(
                            metric_type=MetricType.USAGE_PER_SEAT_30D,
                            value=usage_per_seat_30d,
                            formula_code=FormulaCode.USAGE_PER_SEAT_30D,
                            methodology_version=methodology_version,
                            source_values={
                                "token_usage": str(token_usage),
                                "seat_count": str(inp.seat_count),
                                "days": str(days),
                            },
                            display_scale=DISPLAY_SCALE,
                        )
                    )

            # usage_per_execution: workload scope with workload_count
            if inp.budget_scope == "WORKLOAD":
                if inp.workload_count is not None and inp.workload_count >= 1:
                    workload_count = Decimal(inp.workload_count)
                    usage_per_execution = self._precise_divide(token_usage, workload_count)
                    results.append(
                        NormalizationResult(
                            metric_type=MetricType.USAGE_PER_EXECUTION,
                            value=usage_per_execution,
                            formula_code=FormulaCode.USAGE_PER_EXECUTION,
                            methodology_version=methodology_version,
                            source_values={
                                "token_usage": str(token_usage),
                                "workload_count": str(inp.workload_count),
                            },
                            display_scale=DISPLAY_SCALE,
                        )
                    )

        return results

    @staticmethod
    def _precise_divide(numerator: Decimal, denominator: Decimal) -> Decimal:
        """Divide with 18-digit precision and half-even rounding."""
        # Use quantize for exact precision control
        precision_exp = Decimal(10) ** -NORMALIZATION_PRECISION
        return (numerator / denominator).quantize(precision_exp, rounding=ROUND_HALF_EVEN)

    @staticmethod
    def _unavailable(
        metric_type: MetricType,
        methodology_version: int,
        *,
        missing: list[str],
        sources: dict[str, Any],
    ) -> NormalizationResult:
        """Construct an unavailable result with explicit missing inputs."""
        return NormalizationResult(
            metric_type=metric_type,
            value=None,
            formula_code="",
            methodology_version=methodology_version,
            source_values=sources,
            missing_inputs=missing,
            display_scale=DISPLAY_SCALE,
        )
