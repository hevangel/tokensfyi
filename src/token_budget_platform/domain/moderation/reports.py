"""Community report domain rules.

Requirement 7.7: A community report with category and explanation creates
a Moderation_Case linked to the reported Token_Budget_Record.

Requirement 7.8: Reports alleging Personal_Data or Sensitive_Data exposure
suppress the record from public display BEFORE returning a successful response.

Requirement 4.6: An open dispute report sets Disputed verification status
until the case is resolved.

Requirement 9.5: Sensitive content is redacted from public representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReportCategory(StrEnum):
    """Categories available for community reports."""

    INACCURATE = "INACCURATE"
    OUTDATED = "OUTDATED"
    PERSONAL_DATA = "PERSONAL_DATA"
    SENSITIVE_DATA = "SENSITIVE_DATA"
    ABUSIVE = "ABUSIVE"
    OTHER = "OTHER"


# Categories that trigger synchronous suppression before returning
SENSITIVE_REPORT_CATEGORIES: frozenset[str] = frozenset(
    {ReportCategory.PERSONAL_DATA, ReportCategory.SENSITIVE_DATA}
)

# Minimum explanation length for a valid report
MIN_EXPLANATION_LENGTH = 10
MAX_EXPLANATION_LENGTH = 4000

# Valid report categories as a set for validation
VALID_CATEGORIES: frozenset[str] = frozenset(c.value for c in ReportCategory)


@dataclass(frozen=True, slots=True)
class ReportValidationError:
    """A validation failure in a community report submission."""

    field: str
    message: str


def validate_report(category: str, explanation: str) -> list[ReportValidationError]:
    """Validate a community report submission.

    Returns an empty list if the report is valid, or a list of validation
    errors with field names and corrective messages.
    """
    errors: list[ReportValidationError] = []

    # Category validation
    if not category:
        errors.append(
            ReportValidationError(
                field="category",
                message="Report category is required.",
            )
        )
    elif category not in VALID_CATEGORIES:
        allowed = ", ".join(sorted(VALID_CATEGORIES))
        errors.append(
            ReportValidationError(
                field="category",
                message=f"Invalid report category. Must be one of: {allowed}.",
            )
        )

    # Explanation validation
    if not explanation or not explanation.strip():
        errors.append(
            ReportValidationError(
                field="explanation",
                message="Report explanation is required.",
            )
        )
    elif len(explanation.strip()) < MIN_EXPLANATION_LENGTH:
        errors.append(
            ReportValidationError(
                field="explanation",
                message=f"Explanation must be at least {MIN_EXPLANATION_LENGTH} characters.",
            )
        )
    elif len(explanation) > MAX_EXPLANATION_LENGTH:
        errors.append(
            ReportValidationError(
                field="explanation",
                message=f"Explanation must not exceed {MAX_EXPLANATION_LENGTH} characters.",
            )
        )

    return errors


def is_sensitive_report(category: str) -> bool:
    """Return True if the report category triggers synchronous suppression."""
    return category in SENSITIVE_REPORT_CATEGORIES
