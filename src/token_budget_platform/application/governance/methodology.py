"""Methodology versioning and deterministic recalculation.

Requirements 12.7, 12.8:
- 12.7: Use one documented calculation rule for all records with the same scope.
- 12.8: When a calculation rule changes, recalculate affected metrics and display
         the active methodology version.

Design contract: The active methodology_version and formula/source values
accompany every normalized response.  A rule change creates a new version
and queues deterministic recalculation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    MethodologyVersion,
    NormalizedMetric,
    TokenBudgetRecord,
)
from token_budget_platform.application.governance.normalization import NormalizationService
from token_budget_platform.application.jobs import JobRuntime, TransactionalOutbox
from token_budget_platform.domain.governance.normalization import (
    NormalizationInput,
    NormalizationResult,
)


class MethodologyError(ValueError):
    """Raised when a methodology operation is invalid."""


class MethodologyVersionService:
    """Manages methodology versions and triggers recalculation on rule changes."""

    def __init__(
        self,
        *,
        normalization: NormalizationService | None = None,
        outbox: TransactionalOutbox | None = None,
        job_runtime: JobRuntime | None = None,
    ) -> None:
        self._normalization = normalization or NormalizationService()
        self._outbox = outbox or TransactionalOutbox()
        self._job_runtime = job_runtime

    def active_version(self) -> MethodologyVersion | None:
        """Return the currently active methodology version, if any."""
        return MethodologyVersion.objects.filter(is_active=True).order_by("-version").first()

    def get_version(self, version_number: int) -> MethodologyVersion:
        """Retrieve a specific methodology version by number."""
        try:
            return MethodologyVersion.objects.get(version=version_number)
        except MethodologyVersion.DoesNotExist:
            raise MethodologyError(f"methodology version {version_number} does not exist") from None

    @transaction.atomic
    def create_version(
        self,
        *,
        name: str,
        rules: dict[str, Any],
        activate: bool = False,
    ) -> MethodologyVersion:
        """Create a new methodology version.

        If activate=True, deactivates the current active version and activates
        the new one, then enqueues recalculation of all affected metrics.
        """
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            raise MethodologyError("methodology version requires a name")
        if not isinstance(rules, dict):
            raise MethodologyError("rules must be a dictionary")

        # Determine next version number
        latest = (
            MethodologyVersion.objects.select_for_update()
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        next_version = (latest or 0) + 1
        now = timezone.now()

        mv = MethodologyVersion.objects.create(
            version=next_version,
            name=name,
            rules=rules,
            is_active=activate,
            activated_at=now if activate else None,
        )

        if activate:
            # Deactivate previous active versions
            MethodologyVersion.objects.filter(is_active=True).exclude(id=mv.id).update(
                is_active=False
            )
            # Enqueue recalculation
            self._enqueue_recalculation(mv)

        return mv

    @transaction.atomic
    def activate_version(self, version_number: int) -> MethodologyVersion:
        """Activate an existing methodology version and enqueue recalculation.

        Deactivates the currently active version and triggers deterministic
        recalculation of all affected normalized metrics.
        """
        mv = MethodologyVersion.objects.select_for_update().get(version=version_number)
        if mv.is_active:
            return mv

        # Deactivate all others
        MethodologyVersion.objects.filter(is_active=True).update(is_active=False)

        now = timezone.now()
        mv.is_active = True
        mv.activated_at = now
        mv.save(update_fields=["is_active", "activated_at", "row_version", "updated_at"])

        self._enqueue_recalculation(mv)
        return mv

    def _enqueue_recalculation(self, methodology_version: MethodologyVersion) -> None:
        """Enqueue deterministic recalculation jobs for affected records.

        Each record that has normalized metrics under a previous methodology
        version gets a recalculation job.  Jobs are idempotent by record ID
        and methodology version.
        """
        # Find all records that have metrics under older methodology versions
        affected_record_ids = TokenBudgetRecord.objects.values_list("id", flat=True).iterator()

        for record_id in affected_record_ids:
            idempotency_key = f"recalculate:{record_id}:v{methodology_version.version}"
            if self._job_runtime is not None:
                self._job_runtime.enqueue(
                    job_type="RECALCULATE_METRICS",
                    payload={
                        "record_id": str(record_id),
                        "version": methodology_version.version,
                    },
                    idempotency_key=idempotency_key,
                    priority=30,
                )
            else:
                # Fall back to outbox for eventual processing
                self._outbox.enqueue(
                    aggregate_type="TokenBudgetRecord",
                    aggregate_id=record_id,
                    event_type="RECALCULATE_METRICS",
                    payload={
                        "record_id": str(record_id),
                        "version": methodology_version.version,
                    },
                )

    def recalculate_record(
        self,
        record_id: UUID,
        methodology_version: int | None = None,
    ) -> list[NormalizationResult]:
        """Deterministically recalculate metrics for a single record.

        Uses the specified methodology version, or the currently active one.
        Persists results as NormalizedMetric rows (unique on record/type/version).
        Returns the calculated NormalizationResult list.
        """
        try:
            record = TokenBudgetRecord.objects.get(id=record_id)
        except TokenBudgetRecord.DoesNotExist:
            raise MethodologyError(f"record {record_id} does not exist") from None

        mv: MethodologyVersion | None
        if methodology_version is not None:
            mv = self.get_version(methodology_version)
        else:
            mv = self.active_version()
            if mv is None:
                raise MethodologyError("no active methodology version")

        inp = NormalizationInput(
            record_id=record.id,
            budget_scope=record.budget_scope,
            token_budget=record.token_budget,
            reporting_start=record.reporting_start,
            reporting_end=record.reporting_end,
            seat_count=record.seat_count,
            workload_count=record.workload_count,
            token_usage=record.token_usage,
            methodology_version=mv.version,
        )

        results = self._normalization.normalize(inp)
        now = timezone.now()

        with transaction.atomic():
            for result in results:
                if not result.available:
                    # Do not persist unavailable metrics as database rows
                    continue

                NormalizedMetric.objects.update_or_create(
                    record=record,
                    metric_type=result.metric_type,
                    methodology_version=mv,
                    defaults={
                        "value": result.value,
                        "display_scale": result.display_scale,
                        "formula_code": result.formula_code,
                        "source_values": result.source_values,
                        "calculated_at": now,
                    },
                )

            # Update the record's methodology_version reference
            if record.methodology_version_id != mv.id:
                record.methodology_version = mv
                record.save(update_fields=["methodology_version", "row_version", "updated_at"])

        return results
