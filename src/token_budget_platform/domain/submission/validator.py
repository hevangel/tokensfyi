"""Pure domain submission validator shared by HTML forms, JSON APIs, preview, and persistence.

Validates Unicode-normalized bounded strings, controlled-value IDs, date order/timezone,
source date, integer token/count ranges, ISO 4217 currency, optional-field coherence,
and allocation totals.  Returns field-linked corrective errors while preserving valid
sibling values.

All documented bounds are inclusive unless stated otherwise:
- Token budget and optional usage: 1..10^30-1
- Seat/workload count: 1..10^9
- Money: 0..10^18 minor units with ISO currency exponent
- Reporting start not after end; period at most 3660 days
- Source date not in the future
- Names: 1–200 characters
- Explanations/methodology: 1–4000 characters
- URLs: HTTPS and at most 2048 characters
- Input/output allocations: non-negative, must sum exactly to total budget
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

# ISO 4217 three-letter currency codes (a subset; full list is 180+ active codes).
# We validate structure (three uppercase ASCII letters) and can optionally check
# against a known set.  The design says "ISO 4217 currency" so we validate format.
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_HTTPS_URL_RE = re.compile(r"^https://\S+$", re.ASCII)
_IANA_TIMEZONE_RE = re.compile(r"^[A-Za-z_]+/[A-Za-z0-9_/+-]+$|^UTC$")
_WHITESPACE = re.compile(r"\s+")

# Documented numeric bounds
TOKEN_BUDGET_MIN = Decimal("1")
TOKEN_BUDGET_MAX = Decimal(10**30 - 1)
USAGE_MIN = Decimal("1")
USAGE_MAX = Decimal(10**30 - 1)
ALLOCATION_MIN = Decimal("0")
COUNT_MIN = 1
COUNT_MAX = 10**9
COST_MIN = Decimal("0")
COST_MAX = Decimal(10**18)
MAX_PERIOD_DAYS = 3660
NAME_MAX_LENGTH = 200
EXPLANATION_MAX_LENGTH = 4000
URL_MAX_LENGTH = 2048

BUDGET_SCOPES = frozenset({"INDIVIDUAL", "TEAM", "WORKLOAD", "ORGANIZATION"})
ATTRIBUTION_CHOICES = frozenset({"ANONYMOUS", "ATTRIBUTED"})


@dataclass(frozen=True, slots=True)
class FieldError:
    """A validation error linked to a specific field with a corrective message."""

    field: str
    code: str
    message: str


@dataclass(slots=True)
class ValidationResult:
    """Accumulates field-linked errors; valid when no errors are present."""

    errors: list[FieldError] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def add(self, field_name: str, code: str, message: str) -> None:
        self.errors.append(FieldError(field=field_name, code=code, message=message))

    def fields_with_errors(self) -> set[str]:
        return {e.field for e in self.errors}


def _normalize_text(value: str) -> str:
    """Unicode NFC normalize and collapse internal whitespace."""
    normalized = unicodedata.normalize("NFC", value)
    return _WHITESPACE.sub(" ", normalized).strip()


def _normalize_for_matching(value: str) -> str:
    """NFKC + casefold for controlled-value lookups."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE.sub(" ", normalized).strip()


class SubmissionValidator:
    """Pure domain validator for submission drafts.

    Stateless except for the active controlled-value set provided at construction.
    Shared by HTML step validation, JSON API validation, preview, and confirmation.
    """

    def __init__(
        self,
        *,
        active_controlled_value_ids: dict[str, set[str]] | None = None,
        reference_date: date | None = None,
    ) -> None:
        """Initialize with active controlled-value IDs by kind and a reference date.

        Parameters
        ----------
        active_controlled_value_ids
            Mapping from ControlledValueKind to set of valid UUID strings.
            Example: {"MODEL_PROVIDER": {"uuid1", "uuid2"}, ...}
        reference_date
            The date against which source_date is validated (must not be in the future).
            Defaults to today if not provided.
        """
        self._controlled_values = active_controlled_value_ids or {}
        self._reference_date = reference_date or date.today()

    def validate(self, payload: dict[str, Any]) -> ValidationResult:
        """Validate an entire submission payload and return field-linked errors.

        Valid sibling fields are preserved in the result even when other fields fail.
        """
        result = ValidationResult()

        # --- Required string fields ---
        self._validate_name(result, payload, "organization_name", "Organization name")
        self._validate_name(result, payload, "industry", "Industry")

        # --- Controlled values ---
        self._validate_controlled_value(result, payload, "size_band_id", "SIZE_BAND")
        self._validate_controlled_value(result, payload, "region_id", "REGION")
        self._validate_controlled_value(result, payload, "model_provider_id", "MODEL_PROVIDER")
        self._validate_controlled_value(result, payload, "ai_product_id", "AI_PRODUCT")
        self._validate_controlled_value(
            result, payload, "workload_category_id", "WORKLOAD_CATEGORY"
        )

        # --- Budget scope ---
        budget_scope = self._validate_budget_scope(result, payload)

        # --- Seat/workload count coherence ---
        self._validate_count_coherence(result, payload, budget_scope)

        # --- Token budget ---
        token_budget = self._validate_integer_decimal(
            result,
            payload,
            "token_budget",
            min_val=TOKEN_BUDGET_MIN,
            max_val=TOKEN_BUDGET_MAX,
            required=True,
            label="Token budget",
        )

        # --- Input/output allocation coherence ---
        self._validate_allocation(result, payload, token_budget)

        # --- Reporting period ---
        self._validate_reporting_period(result, payload)

        # --- Source date ---
        self._validate_source_date(result, payload)

        # --- Optional usage ---
        self._validate_optional_usage(result, payload)

        # --- Optional cost ---
        self._validate_optional_cost(result, payload)

        # --- Attribution ---
        self._validate_attribution(result, payload)

        # --- Optional methodology notes ---
        self._validate_optional_text(
            result, payload, "methodology_notes", EXPLANATION_MAX_LENGTH, required=False
        )

        # --- Optional evidence URL ---
        self._validate_optional_url(result, payload, "evidence_url")

        return result

    def validate_step(self, step: str, payload: dict[str, Any]) -> ValidationResult:
        """Validate a single form step for progressive multi-step flows.

        Steps correspond to logical groupings:
        - "organization": organization_name, industry, size_band_id, region_id
        - "product": model_provider_id, ai_product_id, workload_category_id, budget_scope
        - "budget": token_budget, input_tokens, output_tokens, seat_count, workload_count
        - "period": reporting_start, reporting_end, reporting_timezone, source_date
        - "usage": token_usage (optional)
        - "cost": cost_minor_units, cost_currency, cost_basis (optional group)
        - "attribution": contributor_attribution, methodology_notes
        - "evidence": evidence_url (optional)
        - "contact": contact_email (optional)
        """
        result = ValidationResult()

        if step == "organization":
            self._validate_name(result, payload, "organization_name", "Organization name")
            self._validate_name(result, payload, "industry", "Industry")
            self._validate_controlled_value(result, payload, "size_band_id", "SIZE_BAND")
            self._validate_controlled_value(result, payload, "region_id", "REGION")

        elif step == "product":
            self._validate_controlled_value(result, payload, "model_provider_id", "MODEL_PROVIDER")
            self._validate_controlled_value(result, payload, "ai_product_id", "AI_PRODUCT")
            self._validate_controlled_value(
                result, payload, "workload_category_id", "WORKLOAD_CATEGORY"
            )
            self._validate_budget_scope(result, payload)

        elif step == "budget":
            budget_scope = payload.get("budget_scope")
            self._validate_count_coherence(result, payload, budget_scope)
            token_budget = self._validate_integer_decimal(
                result,
                payload,
                "token_budget",
                min_val=TOKEN_BUDGET_MIN,
                max_val=TOKEN_BUDGET_MAX,
                required=True,
                label="Token budget",
            )
            self._validate_allocation(result, payload, token_budget)

        elif step == "period":
            self._validate_reporting_period(result, payload)
            self._validate_source_date(result, payload)

        elif step == "usage":
            self._validate_optional_usage(result, payload)

        elif step == "cost":
            self._validate_optional_cost(result, payload)

        elif step == "attribution":
            self._validate_attribution(result, payload)
            self._validate_optional_text(
                result, payload, "methodology_notes", EXPLANATION_MAX_LENGTH, required=False
            )

        elif step == "evidence":
            self._validate_optional_url(result, payload, "evidence_url")

        elif step == "contact":
            self._validate_optional_email(result, payload, "contact_email")

        return result

    # ------------------------------------------------------------------
    # Private validation helpers
    # ------------------------------------------------------------------

    def _validate_name(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        field_name: str,
        label: str,
    ) -> str | None:
        raw = payload.get(field_name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            result.add(field_name, "required", f"{label} is required.")
            return None
        if not isinstance(raw, str):
            result.add(field_name, "invalid_type", f"{label} must be text.")
            return None
        normalized = _normalize_text(raw)
        if len(normalized) < 1:
            result.add(field_name, "required", f"{label} is required.")
            return None
        if len(normalized) > NAME_MAX_LENGTH:
            result.add(
                field_name,
                "too_long",
                f"{label} must be at most {NAME_MAX_LENGTH} characters.",
            )
            return None
        return normalized

    def _validate_controlled_value(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        field_name: str,
        kind: str,
    ) -> str | None:
        raw = payload.get(field_name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            kind_label = kind.replace("_", " ").lower()
            result.add(field_name, "required", f"Please select a valid {kind_label}.")
            return None
        if not isinstance(raw, str):
            kind_label = kind.replace("_", " ").lower()
            result.add(field_name, "invalid_type", f"{kind_label} must be a valid selection.")
            return None
        value_str = raw.strip()
        # Validate UUID format
        try:
            UUID(value_str)
        except (ValueError, AttributeError):
            result.add(
                field_name,
                "invalid_format",
                f"Invalid {kind.replace('_', ' ').lower()} identifier.",
            )
            return None
        # Check against active values
        active_set = self._controlled_values.get(kind, set())
        if active_set and value_str not in active_set:
            result.add(
                field_name,
                "inactive_value",
                f"Selected {kind.replace('_', ' ').lower()} is not currently active.",
            )
            return None
        return value_str

    def _validate_budget_scope(
        self, result: ValidationResult, payload: dict[str, Any]
    ) -> str | None:
        raw = payload.get("budget_scope")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            result.add("budget_scope", "required", "Budget scope is required.")
            return None
        if not isinstance(raw, str):
            result.add("budget_scope", "invalid_type", "Budget scope must be text.")
            return None
        value = raw.strip().upper()
        if value not in BUDGET_SCOPES:
            result.add(
                "budget_scope",
                "invalid_choice",
                f"Budget scope must be one of: {', '.join(sorted(BUDGET_SCOPES))}.",
            )
            return None
        return value

    def _validate_count_coherence(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        budget_scope: str | None,
    ) -> None:
        """Validate seat_count and workload_count coherence with budget_scope."""
        seat_count = payload.get("seat_count")
        workload_count = payload.get("workload_count")

        # Only one denominator allowed
        has_seat = seat_count is not None and seat_count != "" and seat_count != 0
        has_workload = workload_count is not None and workload_count != "" and workload_count != 0

        if has_seat and has_workload:
            result.add(
                "seat_count",
                "mutual_exclusion",
                "Provide either seat count or workload count, not both.",
            )
            return

        # Workload scope requires workload_count
        if budget_scope == "WORKLOAD":
            if not has_workload:
                result.add(
                    "workload_count",
                    "required_for_scope",
                    "Workload count is required for workload scope.",
                )
                return
        else:
            # Non-workload scope may have seat_count but not workload_count
            if has_workload:
                result.add(
                    "workload_count",
                    "scope_mismatch",
                    "Workload count is only valid for workload scope.",
                )
                return

        # Validate ranges
        if has_seat:
            self._validate_positive_int(
                result, payload, "seat_count", COUNT_MIN, COUNT_MAX, "Seat count"
            )
        if has_workload:
            self._validate_positive_int(
                result, payload, "workload_count", COUNT_MIN, COUNT_MAX, "Workload count"
            )

    def _validate_positive_int(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        field_name: str,
        min_val: int,
        max_val: int,
        label: str,
    ) -> int | None:
        raw = payload.get(field_name)
        if raw is None or raw == "":
            return None
        try:
            value = int(raw)
        except (ValueError, TypeError):
            result.add(field_name, "invalid_integer", f"{label} must be a whole number.")
            return None
        if value < min_val:
            result.add(field_name, "too_small", f"{label} must be at least {min_val}.")
            return None
        if value > max_val:
            result.add(field_name, "too_large", f"{label} must be at most {max_val:,}.")
            return None
        return value

    def _validate_integer_decimal(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        field_name: str,
        *,
        min_val: Decimal,
        max_val: Decimal,
        required: bool,
        label: str,
    ) -> Decimal | None:
        """Validate integer-valued Decimal fields (no fractional component)."""
        raw = payload.get(field_name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if required:
                result.add(field_name, "required", f"{label} is required.")
            return None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            result.add(field_name, "invalid_number", f"{label} must be a valid number.")
            return None
        # Must be integer
        if value != value.to_integral_value():
            result.add(field_name, "not_integer", f"{label} must be a whole number.")
            return None
        if value < min_val:
            result.add(field_name, "too_small", f"{label} must be at least {min_val}.")
            return None
        if value > max_val:
            result.add(field_name, "too_large", f"{label} exceeds the maximum allowed value.")
            return None
        return value

    def _validate_allocation(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        token_budget: Decimal | None,
    ) -> None:
        """Validate input/output token allocations: both or neither, non-negative, sum to budget."""
        input_raw = payload.get("input_tokens")
        output_raw = payload.get("output_tokens")

        has_input = input_raw is not None and input_raw != ""
        has_output = output_raw is not None and output_raw != ""

        # Both or neither
        if has_input != has_output:
            missing = "output_tokens" if has_input else "input_tokens"
            result.add(
                missing,
                "allocation_pair",
                "Both input and output token allocations are required when one is provided.",
            )
            return

        if not has_input:
            return  # Both absent — valid

        input_val = self._validate_integer_decimal(
            result,
            payload,
            "input_tokens",
            min_val=ALLOCATION_MIN,
            max_val=TOKEN_BUDGET_MAX,
            required=False,
            label="Input tokens",
        )
        output_val = self._validate_integer_decimal(
            result,
            payload,
            "output_tokens",
            min_val=ALLOCATION_MIN,
            max_val=TOKEN_BUDGET_MAX,
            required=False,
            label="Output tokens",
        )

        if input_val is None or output_val is None:
            return  # Already has errors

        # Sum must equal token_budget
        if token_budget is not None and (input_val + output_val) != token_budget:
            result.add(
                "input_tokens",
                "allocation_sum",
                "Input and output token allocations must sum to the total token budget.",
            )

    def _validate_reporting_period(self, result: ValidationResult, payload: dict[str, Any]) -> None:
        """Validate reporting_start, reporting_end, reporting_timezone."""
        start = self._parse_date(result, payload, "reporting_start", "Reporting start date")
        end = self._parse_date(result, payload, "reporting_end", "Reporting end date")
        self._validate_timezone(result, payload)

        if start is not None and end is not None:
            if start > end:
                result.add(
                    "reporting_start",
                    "date_order",
                    "Reporting start date must not be after the end date.",
                )
            else:
                period_days = (end - start).days + 1  # inclusive
                if period_days > MAX_PERIOD_DAYS:
                    result.add(
                        "reporting_end",
                        "period_too_long",
                        f"Reporting period must be at most {MAX_PERIOD_DAYS} days.",
                    )

    def _validate_source_date(
        self, result: ValidationResult, payload: dict[str, Any]
    ) -> date | None:
        source = self._parse_date(result, payload, "source_date", "Source date")
        if source is not None and source > self._reference_date:
            result.add(
                "source_date",
                "future_date",
                "Source date must not be in the future.",
            )
            return None
        return source

    def _validate_optional_usage(self, result: ValidationResult, payload: dict[str, Any]) -> None:
        """Validate optional token_usage field."""
        raw = payload.get("token_usage")
        if raw is None or raw == "":
            return
        self._validate_integer_decimal(
            result,
            payload,
            "token_usage",
            min_val=USAGE_MIN,
            max_val=USAGE_MAX,
            required=False,
            label="Token usage",
        )

    def _validate_optional_cost(self, result: ValidationResult, payload: dict[str, Any]) -> None:
        """Validate cost group coherence: all three or none."""
        cost_raw = payload.get("cost_minor_units")
        currency_raw = payload.get("cost_currency")
        basis_raw = payload.get("cost_basis")

        has_cost = cost_raw is not None and cost_raw != ""
        has_currency = currency_raw is not None and (
            isinstance(currency_raw, str) and currency_raw.strip() != ""
        )
        has_basis = basis_raw is not None and (
            isinstance(basis_raw, str) and basis_raw.strip() != ""
        )

        provided = [has_cost, has_currency, has_basis]
        if not any(provided):
            return  # All absent — valid

        if not all(provided):
            # Identify missing fields
            if not has_cost:
                result.add(
                    "cost_minor_units",
                    "cost_coherence",
                    "Cost amount is required when currency or basis is provided.",
                )
            if not has_currency:
                result.add(
                    "cost_currency",
                    "cost_coherence",
                    "Currency code is required when cost amount or basis is provided.",
                )
            if not has_basis:
                result.add(
                    "cost_basis",
                    "cost_coherence",
                    "Cost basis is required when cost amount or currency is provided.",
                )
            return

        # Validate cost amount
        self._validate_integer_decimal(
            result,
            payload,
            "cost_minor_units",
            min_val=COST_MIN,
            max_val=COST_MAX,
            required=False,
            label="Cost amount",
        )

        # Validate currency code (ISO 4217)
        currency = str(currency_raw).strip().upper()
        if not _CURRENCY_RE.match(currency):
            result.add(
                "cost_currency",
                "invalid_currency",
                "Currency must be a valid 3-letter ISO 4217 code (e.g., USD, EUR, GBP).",
            )

        # Validate cost_basis length
        basis = _normalize_text(str(basis_raw))
        if len(basis) > NAME_MAX_LENGTH:
            result.add(
                "cost_basis",
                "too_long",
                f"Cost basis must be at most {NAME_MAX_LENGTH} characters.",
            )

    def _validate_attribution(self, result: ValidationResult, payload: dict[str, Any]) -> None:
        raw = payload.get("contributor_attribution")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            # Default to ANONYMOUS if not provided
            return
        if not isinstance(raw, str):
            result.add(
                "contributor_attribution",
                "invalid_type",
                "Attribution choice must be text.",
            )
            return
        value = raw.strip().upper()
        if value not in ATTRIBUTION_CHOICES:
            result.add(
                "contributor_attribution",
                "invalid_choice",
                f"Attribution must be one of: {', '.join(sorted(ATTRIBUTION_CHOICES))}.",
            )

    def _validate_optional_text(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        field_name: str,
        max_length: int,
        *,
        required: bool,
    ) -> str | None:
        raw = payload.get(field_name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if required:
                result.add(field_name, "required", f"{field_name} is required.")
            return None
        if not isinstance(raw, str):
            result.add(field_name, "invalid_type", f"{field_name} must be text.")
            return None
        normalized = _normalize_text(raw)
        if len(normalized) > max_length:
            result.add(
                field_name,
                "too_long",
                f"Must be at most {max_length} characters.",
            )
            return None
        return normalized

    def _validate_optional_url(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        field_name: str,
    ) -> str | None:
        raw = payload.get(field_name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        if not isinstance(raw, str):
            result.add(field_name, "invalid_type", "URL must be text.")
            return None
        url = raw.strip()
        if len(url) > URL_MAX_LENGTH:
            result.add(
                field_name,
                "too_long",
                f"URL must be at most {URL_MAX_LENGTH} characters.",
            )
            return None
        if not _HTTPS_URL_RE.match(url):
            result.add(
                field_name,
                "invalid_url",
                "URL must use HTTPS protocol.",
            )
            return None
        return url

    def _validate_optional_email(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        field_name: str,
    ) -> str | None:
        raw = payload.get(field_name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        if not isinstance(raw, str):
            result.add(field_name, "invalid_type", "Email must be text.")
            return None
        email = raw.strip()
        if len(email) > NAME_MAX_LENGTH:
            result.add(field_name, "too_long", "Email must be at most 200 characters.")
            return None
        # Basic email format check
        if "@" not in email or "." not in email.split("@")[-1]:
            result.add(field_name, "invalid_email", "Please enter a valid email address.")
            return None
        return email

    def _validate_timezone(self, result: ValidationResult, payload: dict[str, Any]) -> str | None:
        raw = payload.get("reporting_timezone")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            result.add(
                "reporting_timezone",
                "required",
                "Reporting timezone is required.",
            )
            return None
        if not isinstance(raw, str):
            result.add("reporting_timezone", "invalid_type", "Timezone must be text.")
            return None
        tz = raw.strip()
        if not _IANA_TIMEZONE_RE.match(tz):
            result.add(
                "reporting_timezone",
                "invalid_timezone",
                "Timezone must be a valid IANA timezone (e.g., America/New_York) or UTC.",
            )
            return None
        return tz

    def _parse_date(
        self,
        result: ValidationResult,
        payload: dict[str, Any],
        field_name: str,
        label: str,
    ) -> date | None:
        raw = payload.get(field_name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            result.add(field_name, "required", f"{label} is required.")
            return None
        if isinstance(raw, date) and not isinstance(raw, type(None)):
            return raw
        if not isinstance(raw, str):
            result.add(field_name, "invalid_type", f"{label} must be a valid date.")
            return None
        try:
            return date.fromisoformat(raw.strip())
        except ValueError:
            result.add(
                field_name,
                "invalid_date",
                f"{label} must be a valid date in YYYY-MM-DD format.",
            )
            return None
