"""Tests for exact normalization and methodology versioning.

Validates Requirements 3.3, 3.4, 3.5, 12.7, 12.8:
- 3.3: Normalized metrics display with calculation formula and source values.
- 3.4: Missing inputs are explicit and labeled unavailable.
- 3.5: Both original and 30-day normalized values presented.
- 12.7: One documented calculation rule per Budget_Scope.
- 12.8: Rule changes recalculate affected metrics with version tracking.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    ControlledValue,
    ControlledValueKind,
    MethodologyVersion,
    NormalizedMetric,
    Organization,
    TokenBudgetRecord,
)
from token_budget_platform.application.governance.methodology import (
    MethodologyError,
    MethodologyVersionService,
)
from token_budget_platform.application.governance.normalization import NormalizationService
from token_budget_platform.application.jobs import JobRuntime
from token_budget_platform.domain.governance.normalization import (
    NORMALIZATION_PRECISION,
    FormulaCode,
    MetricType,
    NormalizationInput,
)


@pytest.fixture
def normalization_service() -> NormalizationService:
    return NormalizationService()


@pytest.fixture
def now():
    return timezone.now()


@pytest.fixture
def methodology_v1(now):
    return MethodologyVersion.objects.create(
        version=1,
        name="Methodology v1",
        rules={"budget_30d": "token_budget * 30 / days"},
        is_active=True,
        activated_at=now,
    )


@pytest.fixture
def region_value(now):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.REGION,
        code="us-east",
        label="US East",
        normalized_label="us east",
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def size_band_value(now):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.SIZE_BAND,
        code="small",
        label="1-50",
        normalized_label="1-50",
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def provider_value(now):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.MODEL_PROVIDER,
        code="openai",
        label="OpenAI",
        normalized_label="openai",
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def product_value(now, provider_value):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.AI_PRODUCT,
        code="chatgpt",
        label="ChatGPT",
        normalized_label="chatgpt",
        parent=provider_value,
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def workload_value(now):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.WORKLOAD_CATEGORY,
        code="software-dev",
        label="Software Development",
        normalized_label="software development",
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def organization(region_value, size_band_value):
    return Organization.objects.create(
        name="Test Corp",
        normalized_name="test corp",
        slug="test-corp",
        industry="Technology",
        size_band=size_band_value,
        region=region_value,
    )


def _make_input(
    *,
    budget_scope: str = "TEAM",
    token_budget: int | Decimal = 1_000_000,
    start: date | None = None,
    end: date | None = None,
    seat_count: int | None = 10,
    workload_count: int | None = None,
    token_usage: int | Decimal | None = None,
    methodology_version: int = 1,
) -> NormalizationInput:
    """Helper to build a NormalizationInput with sensible defaults."""
    import uuid

    if start is None:
        start = date(2024, 1, 1)
    if end is None:
        end = date(2024, 1, 30)
    return NormalizationInput(
        record_id=uuid.uuid4(),
        budget_scope=budget_scope,
        token_budget=Decimal(token_budget),
        reporting_start=start,
        reporting_end=end,
        seat_count=seat_count,
        workload_count=workload_count,
        token_usage=Decimal(token_usage) if token_usage is not None else None,
        methodology_version=methodology_version,
    )


# --- Requirement 3.3: Normalized metrics with formula and source values ---


class TestBudget30dNormalization:
    """budget_30d = token_budget * 30 / days (inclusive)."""

    def test_same_day_period_equals_one_day(self, normalization_service):
        """A same-day period has days=1, so budget_30d = token_budget * 30."""
        inp = _make_input(
            token_budget=1000,
            start=date(2024, 3, 15),
            end=date(2024, 3, 15),
        )
        results = normalization_service.normalize(inp)
        budget_30d = next(r for r in results if r.metric_type == MetricType.BUDGET_30D)

        assert budget_30d.available
        assert budget_30d.value == Decimal(1000) * 30
        assert budget_30d.formula_code == FormulaCode.BUDGET_30D
        assert budget_30d.source_values["token_budget"] == "1000"
        assert budget_30d.source_values["days"] == "1"

    def test_30_day_period_returns_original_budget(self, normalization_service):
        """A 30-day period normalizes to the original budget."""
        inp = _make_input(
            token_budget=1_000_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),  # 30 days inclusive
        )
        results = normalization_service.normalize(inp)
        budget_30d = next(r for r in results if r.metric_type == MetricType.BUDGET_30D)

        assert budget_30d.value == Decimal("1000000")

    def test_60_day_period_halves_budget(self, normalization_service):
        """A 60-day period normalizes to half the budget."""
        inp = _make_input(
            token_budget=1_200_000,
            start=date(2024, 1, 1),
            end=date(2024, 2, 29),  # 60 days inclusive
        )
        results = normalization_service.normalize(inp)
        budget_30d = next(r for r in results if r.metric_type == MetricType.BUDGET_30D)

        assert budget_30d.value == Decimal("600000")

    def test_formula_code_and_methodology_version_present(self, normalization_service):
        """Every result carries formula_code and methodology_version."""
        inp = _make_input()
        results = normalization_service.normalize(inp)

        for result in results:
            if result.available:
                assert result.formula_code != ""
                assert result.methodology_version == 1


class TestTokensPerSeat30d:
    """tokens_per_seat_30d = token_budget * 30 / (seat_count * days)."""

    def test_team_scope_with_seat_count(self, normalization_service):
        """Team scope with seat_count produces per-seat metric."""
        inp = _make_input(
            budget_scope="TEAM",
            token_budget=300_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),  # 30 days
            seat_count=10,
        )
        results = normalization_service.normalize(inp)
        per_seat = next(r for r in results if r.metric_type == MetricType.TOKENS_PER_SEAT_30D)

        # 300000 * 30 / (10 * 30) = 30000
        assert per_seat.available
        assert per_seat.value == Decimal("30000")
        assert per_seat.formula_code == FormulaCode.TOKENS_PER_SEAT_30D
        assert per_seat.source_values["seat_count"] == "10"

    def test_individual_scope_with_seat_count_one(self, normalization_service):
        """Individual scope with seat_count=1 matches budget_30d."""
        inp = _make_input(
            budget_scope="INDIVIDUAL",
            token_budget=100_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),
            seat_count=1,
        )
        results = normalization_service.normalize(inp)
        budget_30d = next(r for r in results if r.metric_type == MetricType.BUDGET_30D)
        per_seat = next(r for r in results if r.metric_type == MetricType.TOKENS_PER_SEAT_30D)

        assert per_seat.value == budget_30d.value

    def test_organization_scope_produces_per_seat(self, normalization_service):
        """Organization scope with seat_count produces per-seat metric."""
        inp = _make_input(
            budget_scope="ORGANIZATION",
            token_budget=5_000_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),
            seat_count=100,
        )
        results = normalization_service.normalize(inp)
        per_seat = next(r for r in results if r.metric_type == MetricType.TOKENS_PER_SEAT_30D)

        # 5000000 * 30 / (100 * 30) = 50000
        assert per_seat.available
        assert per_seat.value == Decimal("50000")

    def test_workload_scope_does_not_produce_per_seat(self, normalization_service):
        """Workload scope never produces tokens_per_seat_30d."""
        inp = _make_input(
            budget_scope="WORKLOAD",
            token_budget=1_000_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),
            seat_count=None,
            workload_count=50,
        )
        results = normalization_service.normalize(inp)
        metric_types = [r.metric_type for r in results]

        assert MetricType.TOKENS_PER_SEAT_30D not in metric_types


class TestTokensPerExecution:
    """tokens_per_execution = token_budget / workload_count."""

    def test_workload_scope_with_count(self, normalization_service):
        """Workload scope produces per-execution metric."""
        inp = _make_input(
            budget_scope="WORKLOAD",
            token_budget=1_000_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),
            seat_count=None,
            workload_count=200,
        )
        results = normalization_service.normalize(inp)
        per_exec = next(r for r in results if r.metric_type == MetricType.TOKENS_PER_EXECUTION)

        # 1000000 / 200 = 5000
        assert per_exec.available
        assert per_exec.value == Decimal("5000")
        assert per_exec.formula_code == FormulaCode.TOKENS_PER_EXECUTION

    def test_workload_scope_also_produces_budget_30d(self, normalization_service):
        """Workload scope always includes budget_30d for period comparison."""
        inp = _make_input(
            budget_scope="WORKLOAD",
            token_budget=1_000_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),
            seat_count=None,
            workload_count=200,
        )
        results = normalization_service.normalize(inp)
        budget_30d = next((r for r in results if r.metric_type == MetricType.BUDGET_30D), None)

        assert budget_30d is not None
        assert budget_30d.available

    def test_team_scope_does_not_produce_per_execution(self, normalization_service):
        """Non-workload scopes never produce per-execution metric."""
        inp = _make_input(budget_scope="TEAM", workload_count=None)
        results = normalization_service.normalize(inp)
        metric_types = [r.metric_type for r in results]

        assert MetricType.TOKENS_PER_EXECUTION not in metric_types


# --- Requirement 3.4: Missing inputs are explicit ---


class TestMissingInputs:
    """Missing inputs reported explicitly without imputation."""

    def test_missing_seat_count_for_team(self, normalization_service):
        """Team scope without seat_count reports it as missing."""
        inp = _make_input(budget_scope="TEAM", seat_count=None)
        results = normalization_service.normalize(inp)
        per_seat = next(
            (r for r in results if r.metric_type == MetricType.TOKENS_PER_SEAT_30D), None
        )

        assert per_seat is not None
        assert not per_seat.available
        assert "seat_count" in per_seat.missing_inputs

    def test_missing_workload_count_for_workload(self, normalization_service):
        """Workload scope without workload_count reports it as missing."""
        inp = _make_input(
            budget_scope="WORKLOAD",
            seat_count=None,
            workload_count=None,
        )
        results = normalization_service.normalize(inp)
        per_exec = next(
            (r for r in results if r.metric_type == MetricType.TOKENS_PER_EXECUTION), None
        )

        assert per_exec is not None
        assert not per_exec.available
        assert "workload_count" in per_exec.missing_inputs

    def test_no_usage_metrics_when_usage_is_none(self, normalization_service):
        """No usage metrics are produced when token_usage is None."""
        inp = _make_input(token_usage=None)
        results = normalization_service.normalize(inp)
        usage_types = [
            r.metric_type
            for r in results
            if r.metric_type in (MetricType.USAGE_30D, MetricType.USAGE_PER_SEAT_30D)
        ]

        assert usage_types == []

    def test_unavailable_results_have_no_value(self, normalization_service):
        """Unavailable results have value=None and missing_inputs populated."""
        inp = _make_input(budget_scope="TEAM", seat_count=None)
        results = normalization_service.normalize(inp)

        unavailable = [r for r in results if not r.available]
        assert len(unavailable) >= 1
        for result in unavailable:
            assert result.value is None
            assert len(result.missing_inputs) > 0


# --- Requirement 3.5: Usage normalization ---


class TestUsageNormalization:
    """Usage metrics use equivalent formulas when usage exists."""

    def test_usage_30d_calculated(self, normalization_service):
        """usage_30d = token_usage * 30 / days."""
        inp = _make_input(
            token_budget=1_000_000,
            token_usage=800_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),
        )
        results = normalization_service.normalize(inp)
        usage_30d = next(r for r in results if r.metric_type == MetricType.USAGE_30D)

        # 800000 * 30 / 30 = 800000
        assert usage_30d.available
        assert usage_30d.value == Decimal("800000")
        assert usage_30d.formula_code == FormulaCode.USAGE_30D

    def test_usage_per_seat_30d(self, normalization_service):
        """usage_per_seat_30d = token_usage * 30 / (seat_count * days)."""
        inp = _make_input(
            budget_scope="TEAM",
            token_budget=1_000_000,
            token_usage=600_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),
            seat_count=10,
        )
        results = normalization_service.normalize(inp)
        usage_per_seat = next(r for r in results if r.metric_type == MetricType.USAGE_PER_SEAT_30D)

        # 600000 * 30 / (10 * 30) = 60000
        assert usage_per_seat.available
        assert usage_per_seat.value == Decimal("60000")

    def test_usage_per_execution(self, normalization_service):
        """usage_per_execution = token_usage / workload_count."""
        inp = _make_input(
            budget_scope="WORKLOAD",
            token_budget=1_000_000,
            token_usage=500_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 30),
            seat_count=None,
            workload_count=100,
        )
        results = normalization_service.normalize(inp)
        usage_per_exec = next(r for r in results if r.metric_type == MetricType.USAGE_PER_EXECUTION)

        # 500000 / 100 = 5000
        assert usage_per_exec.available
        assert usage_per_exec.value == Decimal("5000")


# --- Decimal precision ---


class TestDecimalPrecision:
    """Values use Decimal with 18-digit precision and half-even rounding."""

    def test_precision_is_18_digits(self, normalization_service):
        """Non-integer results have exactly 18 decimal places."""
        inp = _make_input(
            token_budget=1_000_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 6),  # 6 days inclusive -> 1000000*30/6 = 5000000
        )
        results = normalization_service.normalize(inp)
        budget_30d = next(r for r in results if r.metric_type == MetricType.BUDGET_30D)

        # This divides evenly, but precision should still be 18 digits
        assert budget_30d.value is not None
        # Check that the exponent matches 18 decimal places
        _, _, exponent = budget_30d.value.as_tuple()
        assert exponent == -NORMALIZATION_PRECISION

    def test_non_integer_division_uses_half_even(self, normalization_service):
        """Non-integer division rounds with ROUND_HALF_EVEN."""
        # 7 days inclusive: 1000000 * 30 / 7 = 4285714.285714285714285714...
        inp = _make_input(
            token_budget=1_000_000,
            start=date(2024, 1, 1),
            end=date(2024, 1, 7),
        )
        results = normalization_service.normalize(inp)
        budget_30d = next(r for r in results if r.metric_type == MetricType.BUDGET_30D)

        expected = (Decimal("1000000") * 30 / Decimal(7)).quantize(
            Decimal(10) ** -NORMALIZATION_PRECISION, rounding=ROUND_HALF_EVEN
        )
        assert budget_30d.value == expected

    def test_no_binary_floating_point(self, normalization_service):
        """Result is a Decimal, never a float."""
        inp = _make_input(token_budget=333_333)
        results = normalization_service.normalize(inp)
        for result in results:
            if result.value is not None:
                assert isinstance(result.value, Decimal)


# --- Requirement 12.7, 12.8: Methodology versioning ---


@pytest.mark.django_db
class TestMethodologyVersioning:
    """Methodology version management and recalculation."""

    def test_create_version(self, now):
        """Creates a new methodology version with sequential numbering."""
        svc = MethodologyVersionService()
        mv = svc.create_version(name="Test v1", rules={"budget_30d": "v1"})

        assert mv.version == 1
        assert mv.name == "Test v1"
        assert mv.is_active is False

    def test_create_and_activate(self, now):
        """Creating with activate=True sets the version as active."""
        svc = MethodologyVersionService()
        mv = svc.create_version(
            name="Active v1",
            rules={"budget_30d": "v1"},
            activate=True,
        )

        assert mv.is_active is True
        assert mv.activated_at is not None

    def test_activate_deactivates_previous(self, now):
        """Activating a new version deactivates the old one."""
        svc = MethodologyVersionService()
        mv1 = svc.create_version(name="v1", rules={}, activate=True)
        mv2 = svc.create_version(name="v2", rules={})
        svc.activate_version(mv2.version)

        mv1.refresh_from_db()
        mv2.refresh_from_db()
        assert mv1.is_active is False
        assert mv2.is_active is True

    def test_active_version_returns_current(self, methodology_v1):
        """active_version returns the currently active one."""
        svc = MethodologyVersionService()
        result = svc.active_version()
        assert result is not None
        assert result.version == methodology_v1.version

    def test_create_version_requires_name(self):
        """Empty name raises MethodologyError."""
        svc = MethodologyVersionService()
        with pytest.raises(MethodologyError, match="name"):
            svc.create_version(name="", rules={})

    def test_sequential_version_numbers(self):
        """Versions are numbered sequentially."""
        svc = MethodologyVersionService()
        mv1 = svc.create_version(name="v1", rules={})
        mv2 = svc.create_version(name="v2", rules={})
        mv3 = svc.create_version(name="v3", rules={})

        assert mv1.version == 1
        assert mv2.version == 2
        assert mv3.version == 3


@pytest.mark.django_db
class TestRecalculation:
    """Deterministic recalculation persists NormalizedMetric rows."""

    def test_recalculate_persists_metrics(
        self,
        organization,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_v1,
        now,
    ):
        """Recalculating a record persists NormalizedMetric rows."""
        record = TokenBudgetRecord.objects.create(
            organization=organization,
            budget_scope="TEAM",
            seat_count=10,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="test corp",
            provider_search="openai",
            product_search="chatgpt",
            token_budget=Decimal("1000000"),
            reporting_start=now.date() - timedelta(days=29),
            reporting_end=now.date(),  # 30 days inclusive
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_v1,
        )

        svc = MethodologyVersionService()
        results = svc.recalculate_record(record.id)

        # Should have budget_30d and tokens_per_seat_30d
        available = [r for r in results if r.available]
        assert len(available) >= 2

        # Check persisted metrics
        metrics = NormalizedMetric.objects.filter(record=record)
        assert metrics.count() >= 2

        budget_metric = metrics.get(metric_type=MetricType.BUDGET_30D)
        assert budget_metric.value == Decimal("1000000")
        assert budget_metric.formula_code == FormulaCode.BUDGET_30D
        assert budget_metric.methodology_version == methodology_v1

    def test_recalculate_updates_on_version_change(
        self,
        organization,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_v1,
        now,
    ):
        """Recalculating with a new version updates existing metric rows."""
        record = TokenBudgetRecord.objects.create(
            organization=organization,
            budget_scope="TEAM",
            seat_count=10,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="test corp",
            provider_search="openai",
            product_search="chatgpt",
            token_budget=Decimal("1000000"),
            reporting_start=now.date() - timedelta(days=29),
            reporting_end=now.date(),
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_v1,
        )

        svc = MethodologyVersionService()
        svc.recalculate_record(record.id, methodology_version=1)

        # Create v2 and recalculate
        mv2 = MethodologyVersion.objects.create(
            version=2,
            name="Methodology v2",
            rules={"budget_30d": "v2"},
            is_active=True,
            activated_at=now,
        )
        methodology_v1.is_active = False
        methodology_v1.save()

        svc.recalculate_record(record.id, methodology_version=2)

        # Metrics for v2 exist
        metrics_v2 = NormalizedMetric.objects.filter(record=record, methodology_version=mv2)
        assert metrics_v2.count() >= 2

    def test_recalculate_nonexistent_record_raises(self, methodology_v1):
        """Recalculating a non-existent record raises MethodologyError."""
        import uuid

        svc = MethodologyVersionService()
        with pytest.raises(MethodologyError, match="does not exist"):
            svc.recalculate_record(uuid.uuid4())

    def test_unavailable_metrics_not_persisted(
        self,
        organization,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_v1,
        now,
    ):
        """Unavailable metrics (missing inputs) are not stored as DB rows."""
        # Team scope without seat_count -> tokens_per_seat_30d unavailable
        record = TokenBudgetRecord.objects.create(
            organization=organization,
            budget_scope="TEAM",
            seat_count=None,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="test corp",
            provider_search="openai",
            product_search="chatgpt",
            token_budget=Decimal("1000000"),
            reporting_start=now.date() - timedelta(days=29),
            reporting_end=now.date(),
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_v1,
        )

        svc = MethodologyVersionService()
        results = svc.recalculate_record(record.id)

        unavailable = [r for r in results if not r.available]
        assert len(unavailable) >= 1

        # Unavailable metrics should NOT be in the database
        metric_types_in_db = set(
            NormalizedMetric.objects.filter(record=record).values_list("metric_type", flat=True)
        )
        for result in unavailable:
            assert result.metric_type not in metric_types_in_db


@pytest.mark.django_db
class TestRecalculationEnqueue:
    """Rule changes enqueue deterministic recalculation jobs."""

    def test_activate_enqueues_recalculation_jobs(
        self,
        organization,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_v1,
        now,
    ):
        """Activating a new version enqueues jobs for affected records."""
        from token_budget_platform.adapters.django.models import Job

        TokenBudgetRecord.objects.create(
            organization=organization,
            budget_scope="TEAM",
            seat_count=10,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="test corp",
            provider_search="openai",
            product_search="chatgpt",
            token_budget=Decimal("1000000"),
            reporting_start=now.date() - timedelta(days=29),
            reporting_end=now.date(),
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_v1,
        )

        job_runtime = JobRuntime(worker_id="test-worker")
        svc = MethodologyVersionService(job_runtime=job_runtime)

        mv2 = svc.create_version(name="v2", rules={"budget_30d": "v2"}, activate=True)

        # Check that a recalculation job was enqueued
        jobs = Job.objects.filter(job_type="RECALCULATE_METRICS")
        assert jobs.count() >= 1
        job = jobs.first()
        assert job is not None
        assert job.payload["version"] == mv2.version

    def test_activate_uses_outbox_when_no_runtime(
        self,
        organization,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_v1,
        now,
    ):
        """Without a JobRuntime, recalculation uses the transactional outbox."""
        from token_budget_platform.adapters.django.models import OutboxEvent

        TokenBudgetRecord.objects.create(
            organization=organization,
            budget_scope="INDIVIDUAL",
            seat_count=1,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="test corp",
            provider_search="openai",
            product_search="chatgpt",
            token_budget=Decimal("500000"),
            reporting_start=now.date() - timedelta(days=29),
            reporting_end=now.date(),
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_v1,
        )

        svc = MethodologyVersionService(job_runtime=None)
        mv2 = svc.create_version(name="v2", rules={}, activate=True)

        events = OutboxEvent.objects.filter(event_type="RECALCULATE_METRICS")
        assert events.count() >= 1
        event = events.first()
        assert event is not None
        assert event.payload["version"] == mv2.version
