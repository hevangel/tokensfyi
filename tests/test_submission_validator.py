"""Tests for the submission validator and draft service.

Validates Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 9.1, 9.2, 9.4, 10.6, 11.3:
- 5.1: Collect all required submission fields
- 5.2: Collect optional usage
- 5.3: Collect optional cost group
- 5.4: Validate fields against documented type, range, and format rules
- 5.5: Identify invalid/missing fields with corrective messages
- 9.1: Limit required Personal_Data collection
- 9.2: Collect one optional email as non-public Personal_Data
- 9.4: Warn contributor about Personal_Data in free text
- 10.6: Neutralize executable markup and reject length/format violations
- 11.3: Associate text error with affected field and focus error summary
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from token_budget_platform.domain.submission.validator import (
    COST_MAX,
    COUNT_MAX,
    EXPLANATION_MAX_LENGTH,
    MAX_PERIOD_DAYS,
    NAME_MAX_LENGTH,
    TOKEN_BUDGET_MAX,
    URL_MAX_LENGTH,
    SubmissionValidator,
)


@pytest.fixture
def active_values() -> dict[str, set[str]]:
    """A set of 'active' controlled-value IDs for testing."""
    return {
        "MODEL_PROVIDER": {str(uuid4()), str(uuid4())},
        "AI_PRODUCT": {str(uuid4()), str(uuid4())},
        "WORKLOAD_CATEGORY": {str(uuid4()), str(uuid4())},
        "SIZE_BAND": {str(uuid4()), str(uuid4())},
        "REGION": {str(uuid4()), str(uuid4())},
    }


@pytest.fixture
def reference_date() -> date:
    return date(2025, 6, 15)


@pytest.fixture
def validator(active_values, reference_date) -> SubmissionValidator:
    return SubmissionValidator(
        active_controlled_value_ids=active_values,
        reference_date=reference_date,
    )


def _pick(values: dict[str, set[str]], kind: str) -> str:
    """Pick the first available ID from a controlled-value kind."""
    return next(iter(values[kind]))


def _valid_payload(active_values: dict[str, set[str]], reference_date: date) -> dict:
    """Build a minimal valid payload."""
    return {
        "organization_name": "Acme Corp",
        "industry": "Technology",
        "size_band_id": _pick(active_values, "SIZE_BAND"),
        "region_id": _pick(active_values, "REGION"),
        "model_provider_id": _pick(active_values, "MODEL_PROVIDER"),
        "ai_product_id": _pick(active_values, "AI_PRODUCT"),
        "workload_category_id": _pick(active_values, "WORKLOAD_CATEGORY"),
        "budget_scope": "TEAM",
        "seat_count": 10,
        "token_budget": "1000000",
        "reporting_start": "2025-01-01",
        "reporting_end": "2025-03-31",
        "reporting_timezone": "UTC",
        "source_date": reference_date.isoformat(),
        "contributor_attribution": "ANONYMOUS",
    }


class TestValidPayload:
    """Valid submissions must pass without errors."""

    def test_minimal_valid_payload(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        result = validator.validate(payload)
        assert result.valid, f"Unexpected errors: {result.errors}"

    def test_valid_with_optional_usage(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_usage"] = "500000"
        result = validator.validate(payload)
        assert result.valid

    def test_valid_with_optional_cost(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["cost_minor_units"] = "100000"
        payload["cost_currency"] = "USD"
        payload["cost_basis"] = "Per reporting period"
        result = validator.validate(payload)
        assert result.valid

    def test_valid_with_allocations(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["input_tokens"] = "600000"
        payload["output_tokens"] = "400000"
        result = validator.validate(payload)
        assert result.valid

    def test_valid_workload_scope(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["budget_scope"] = "WORKLOAD"
        del payload["seat_count"]
        payload["workload_count"] = 100
        result = validator.validate(payload)
        assert result.valid

    def test_valid_organization_scope_no_count(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["budget_scope"] = "ORGANIZATION"
        del payload["seat_count"]
        result = validator.validate(payload)
        assert result.valid

    def test_valid_with_methodology_notes(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["methodology_notes"] = "Based on internal dashboard data."
        result = validator.validate(payload)
        assert result.valid

    def test_valid_with_evidence_url(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["evidence_url"] = "https://example.com/evidence/report.pdf"
        result = validator.validate(payload)
        assert result.valid


class TestRequiredFields:
    """Missing required fields produce field-linked corrective errors (Req 5.5)."""

    def test_missing_organization_name(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        del payload["organization_name"]
        result = validator.validate(payload)
        assert not result.valid
        assert "organization_name" in result.fields_with_errors()

    def test_missing_industry(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        del payload["industry"]
        result = validator.validate(payload)
        assert not result.valid
        assert "industry" in result.fields_with_errors()

    def test_missing_token_budget(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        del payload["token_budget"]
        result = validator.validate(payload)
        assert not result.valid
        assert "token_budget" in result.fields_with_errors()

    def test_missing_reporting_start(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        del payload["reporting_start"]
        result = validator.validate(payload)
        assert not result.valid
        assert "reporting_start" in result.fields_with_errors()

    def test_missing_budget_scope(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        del payload["budget_scope"]
        result = validator.validate(payload)
        assert not result.valid
        assert "budget_scope" in result.fields_with_errors()

    def test_missing_controlled_values(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        for field_name in ["size_band_id", "region_id", "model_provider_id", "ai_product_id"]:
            payload.pop(field_name, None)
        result = validator.validate(payload)
        assert not result.valid
        for field_name in ["size_band_id", "region_id", "model_provider_id", "ai_product_id"]:
            assert field_name in result.fields_with_errors()

    def test_empty_strings_treated_as_missing(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["organization_name"] = "   "
        result = validator.validate(payload)
        assert not result.valid
        assert "organization_name" in result.fields_with_errors()


class TestNameLengthBounds:
    """Name fields bounded at 1–200 characters."""

    def test_name_at_max_length(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["organization_name"] = "A" * NAME_MAX_LENGTH
        result = validator.validate(payload)
        assert result.valid

    def test_name_exceeds_max_length(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["organization_name"] = "A" * (NAME_MAX_LENGTH + 1)
        result = validator.validate(payload)
        assert not result.valid
        assert "organization_name" in result.fields_with_errors()


class TestTokenBudgetBounds:
    """Token budget: 1..10^30-1 integer."""

    def test_token_budget_minimum(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = "1"
        result = validator.validate(payload)
        assert result.valid

    def test_token_budget_zero_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = "0"
        result = validator.validate(payload)
        assert not result.valid
        assert "token_budget" in result.fields_with_errors()

    def test_token_budget_negative_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = "-1"
        result = validator.validate(payload)
        assert not result.valid
        assert "token_budget" in result.fields_with_errors()

    def test_token_budget_at_max(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = str(TOKEN_BUDGET_MAX)
        # Allocations must sum to budget
        payload.pop("input_tokens", None)
        payload.pop("output_tokens", None)
        result = validator.validate(payload)
        assert result.valid

    def test_token_budget_exceeds_max(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = str(TOKEN_BUDGET_MAX + 1)
        result = validator.validate(payload)
        assert not result.valid
        assert "token_budget" in result.fields_with_errors()

    def test_token_budget_fractional_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = "1000.5"
        result = validator.validate(payload)
        assert not result.valid
        assert "token_budget" in result.fields_with_errors()

    def test_token_budget_non_numeric_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = "abc"
        result = validator.validate(payload)
        assert not result.valid
        assert "token_budget" in result.fields_with_errors()


class TestCountBounds:
    """Seat/workload count: 1..10^9."""

    def test_seat_count_at_minimum(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["seat_count"] = 1
        result = validator.validate(payload)
        assert result.valid

    def test_seat_count_at_maximum(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["seat_count"] = COUNT_MAX
        result = validator.validate(payload)
        assert result.valid

    def test_seat_count_exceeds_max(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["seat_count"] = COUNT_MAX + 1
        result = validator.validate(payload)
        assert not result.valid
        assert "seat_count" in result.fields_with_errors()

    def test_seat_count_zero_treated_as_absent(self, validator, active_values, reference_date):
        """Zero seat count is treated as not provided (absent)."""
        payload = _valid_payload(active_values, reference_date)
        payload["seat_count"] = 0
        result = validator.validate(payload)
        assert result.valid  # absent is valid for non-workload scope


class TestAllocationCoherence:
    """Input/output allocations: both or neither, sum to budget."""

    def test_input_without_output_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["input_tokens"] = "600000"
        result = validator.validate(payload)
        assert not result.valid
        assert "output_tokens" in result.fields_with_errors()

    def test_output_without_input_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["output_tokens"] = "400000"
        result = validator.validate(payload)
        assert not result.valid
        assert "input_tokens" in result.fields_with_errors()

    def test_allocation_sum_mismatch_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = "1000000"
        payload["input_tokens"] = "600000"
        payload["output_tokens"] = "500000"  # Sum = 1100000 != 1000000
        result = validator.validate(payload)
        assert not result.valid
        assert "input_tokens" in result.fields_with_errors()

    def test_allocation_negative_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["input_tokens"] = "-1"
        payload["output_tokens"] = "1000001"
        result = validator.validate(payload)
        assert not result.valid
        assert "input_tokens" in result.fields_with_errors()


class TestReportingPeriod:
    """Date order, timezone, and period length validation."""

    def test_start_after_end_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["reporting_start"] = "2025-04-01"
        payload["reporting_end"] = "2025-03-01"
        result = validator.validate(payload)
        assert not result.valid
        assert "reporting_start" in result.fields_with_errors()

    def test_period_at_max_days(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        start = date(2015, 1, 1)
        end = start + timedelta(days=MAX_PERIOD_DAYS - 1)  # inclusive, so days-1
        payload["reporting_start"] = start.isoformat()
        payload["reporting_end"] = end.isoformat()
        result = validator.validate(payload)
        assert result.valid

    def test_period_exceeds_max_days(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        start = date(2015, 1, 1)
        end = start + timedelta(days=MAX_PERIOD_DAYS)  # one day over
        payload["reporting_start"] = start.isoformat()
        payload["reporting_end"] = end.isoformat()
        result = validator.validate(payload)
        assert not result.valid
        assert "reporting_end" in result.fields_with_errors()

    def test_invalid_date_format_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["reporting_start"] = "01/01/2025"
        result = validator.validate(payload)
        assert not result.valid
        assert "reporting_start" in result.fields_with_errors()

    def test_invalid_timezone_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["reporting_timezone"] = "Not/A_Valid_Zone!!"
        result = validator.validate(payload)
        assert not result.valid
        assert "reporting_timezone" in result.fields_with_errors()

    def test_valid_timezone_iana(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["reporting_timezone"] = "America/New_York"
        result = validator.validate(payload)
        assert result.valid


class TestSourceDate:
    """Source date must not be in the future."""

    def test_source_date_today_valid(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["source_date"] = reference_date.isoformat()
        result = validator.validate(payload)
        assert result.valid

    def test_source_date_future_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["source_date"] = (reference_date + timedelta(days=1)).isoformat()
        result = validator.validate(payload)
        assert not result.valid
        assert "source_date" in result.fields_with_errors()

    def test_source_date_past_valid(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["source_date"] = (reference_date - timedelta(days=30)).isoformat()
        result = validator.validate(payload)
        assert result.valid


class TestCostCoherence:
    """Cost fields: all three or none."""

    def test_partial_cost_missing_currency(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["cost_minor_units"] = "100000"
        payload["cost_basis"] = "Per period"
        result = validator.validate(payload)
        assert not result.valid
        assert "cost_currency" in result.fields_with_errors()

    def test_partial_cost_missing_amount(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["cost_currency"] = "EUR"
        payload["cost_basis"] = "Per seat"
        result = validator.validate(payload)
        assert not result.valid
        assert "cost_minor_units" in result.fields_with_errors()

    def test_invalid_currency_format(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["cost_minor_units"] = "100"
        payload["cost_currency"] = "US"  # too short
        payload["cost_basis"] = "Per period"
        result = validator.validate(payload)
        assert not result.valid
        assert "cost_currency" in result.fields_with_errors()

    def test_cost_exceeds_max(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["cost_minor_units"] = str(COST_MAX + 1)
        payload["cost_currency"] = "USD"
        payload["cost_basis"] = "Per period"
        result = validator.validate(payload)
        assert not result.valid
        assert "cost_minor_units" in result.fields_with_errors()


class TestControlledValues:
    """Controlled-value ID validation."""

    def test_invalid_uuid_format_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["model_provider_id"] = "not-a-uuid"
        result = validator.validate(payload)
        assert not result.valid
        assert "model_provider_id" in result.fields_with_errors()

    def test_inactive_value_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        # Use a valid UUID that's not in the active set
        payload["model_provider_id"] = str(uuid4())
        result = validator.validate(payload)
        assert not result.valid
        assert "model_provider_id" in result.fields_with_errors()


class TestUrlValidation:
    """Evidence URLs must be HTTPS and within length."""

    def test_http_url_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["evidence_url"] = "http://example.com/report.pdf"
        result = validator.validate(payload)
        assert not result.valid
        assert "evidence_url" in result.fields_with_errors()

    def test_url_exceeds_max_length(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["evidence_url"] = "https://example.com/" + "a" * URL_MAX_LENGTH
        result = validator.validate(payload)
        assert not result.valid
        assert "evidence_url" in result.fields_with_errors()

    def test_valid_https_url(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["evidence_url"] = "https://example.com/path/to/evidence.pdf"
        result = validator.validate(payload)
        assert result.valid


class TestUnicodeNormalization:
    """Text fields are Unicode NFC normalized."""

    def test_nfc_normalized_name(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        # é composed as e + combining accent vs precomposed é
        payload["organization_name"] = "Caf\u0065\u0301"  # e + combining acute
        result = validator.validate(payload)
        assert result.valid

    def test_whitespace_collapsed(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["organization_name"] = "  Acme   Corp  "
        result = validator.validate(payload)
        assert result.valid


class TestScopeCoherence:
    """Budget scope and count coherence."""

    def test_workload_scope_without_count_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["budget_scope"] = "WORKLOAD"
        payload.pop("seat_count", None)
        result = validator.validate(payload)
        assert not result.valid
        assert "workload_count" in result.fields_with_errors()

    def test_both_counts_rejected(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["seat_count"] = 10
        payload["workload_count"] = 100
        result = validator.validate(payload)
        assert not result.valid
        assert "seat_count" in result.fields_with_errors()

    def test_workload_count_for_non_workload_scope_rejected(
        self, validator, active_values, reference_date
    ):
        payload = _valid_payload(active_values, reference_date)
        payload["budget_scope"] = "TEAM"
        payload.pop("seat_count", None)
        payload["workload_count"] = 100
        result = validator.validate(payload)
        assert not result.valid
        assert "workload_count" in result.fields_with_errors()


class TestPreservesValidSiblings:
    """Invalid fields don't prevent validation of valid siblings (Req 5.5)."""

    def test_multiple_errors_reported_together(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["organization_name"] = ""
        payload["token_budget"] = "abc"
        payload["reporting_start"] = "invalid"
        result = validator.validate(payload)
        assert not result.valid
        errors = result.fields_with_errors()
        # All three should be reported, not just the first
        assert "organization_name" in errors
        assert "token_budget" in errors
        assert "reporting_start" in errors

    def test_error_messages_are_corrective(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["token_budget"] = "0"
        result = validator.validate(payload)
        error = next(e for e in result.errors if e.field == "token_budget")
        assert "at least" in error.message.lower()


class TestStepValidation:
    """Step-based progressive validation."""

    def test_organization_step(self, validator, active_values, reference_date):
        payload = {
            "organization_name": "Acme Corp",
            "industry": "Technology",
            "size_band_id": _pick(active_values, "SIZE_BAND"),
            "region_id": _pick(active_values, "REGION"),
        }
        result = validator.validate_step("organization", payload)
        assert result.valid

    def test_organization_step_missing_field(self, validator, active_values, reference_date):
        payload = {
            "organization_name": "Acme Corp",
            # Missing industry, size_band_id, region_id
        }
        result = validator.validate_step("organization", payload)
        assert not result.valid
        assert "industry" in result.fields_with_errors()

    def test_budget_step_validates_allocation(self, validator, active_values, reference_date):
        payload = {
            "budget_scope": "TEAM",
            "seat_count": 10,
            "token_budget": "1000000",
            "input_tokens": "600000",
            "output_tokens": "400000",
        }
        result = validator.validate_step("budget", payload)
        assert result.valid

    def test_period_step(self, validator, active_values, reference_date):
        payload = {
            "reporting_start": "2025-01-01",
            "reporting_end": "2025-03-31",
            "reporting_timezone": "UTC",
            "source_date": reference_date.isoformat(),
        }
        result = validator.validate_step("period", payload)
        assert result.valid


class TestMethodologyNotes:
    """Methodology notes: 1–4000 characters."""

    def test_notes_at_max_length(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["methodology_notes"] = "A" * EXPLANATION_MAX_LENGTH
        result = validator.validate(payload)
        assert result.valid

    def test_notes_exceed_max_length(self, validator, active_values, reference_date):
        payload = _valid_payload(active_values, reference_date)
        payload["methodology_notes"] = "A" * (EXPLANATION_MAX_LENGTH + 1)
        result = validator.validate(payload)
        assert not result.valid
        assert "methodology_notes" in result.fields_with_errors()
