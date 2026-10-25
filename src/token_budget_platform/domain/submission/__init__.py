"""Contributor submission domain boundary.

Exports the pure-domain SubmissionValidator, DuplicateDetector, and value
types shared by HTML forms, JSON APIs, preview, and persistence.
"""

from token_budget_platform.domain.submission.duplicate_detector import (
    FINGERPRINT_VERSION,
    DuplicateFingerprint,
    check_duplicate,
    compute_fingerprint,
)
from token_budget_platform.domain.submission.types import (
    DraftResult,
    SubmissionPayload,
)
from token_budget_platform.domain.submission.validator import (
    ALLOCATION_MIN,
    ATTRIBUTION_CHOICES,
    BUDGET_SCOPES,
    COST_MAX,
    COST_MIN,
    COUNT_MAX,
    COUNT_MIN,
    EXPLANATION_MAX_LENGTH,
    MAX_PERIOD_DAYS,
    NAME_MAX_LENGTH,
    TOKEN_BUDGET_MAX,
    TOKEN_BUDGET_MIN,
    URL_MAX_LENGTH,
    USAGE_MAX,
    USAGE_MIN,
    FieldError,
    SubmissionValidator,
    ValidationResult,
)

__all__ = [
    "ALLOCATION_MIN",
    "ATTRIBUTION_CHOICES",
    "BUDGET_SCOPES",
    "COST_MAX",
    "COST_MIN",
    "COUNT_MAX",
    "COUNT_MIN",
    "DraftResult",
    "DuplicateFingerprint",
    "EXPLANATION_MAX_LENGTH",
    "FINGERPRINT_VERSION",
    "FieldError",
    "MAX_PERIOD_DAYS",
    "NAME_MAX_LENGTH",
    "SubmissionPayload",
    "SubmissionValidator",
    "TOKEN_BUDGET_MAX",
    "TOKEN_BUDGET_MIN",
    "URL_MAX_LENGTH",
    "USAGE_MAX",
    "USAGE_MIN",
    "ValidationResult",
    "check_duplicate",
    "compute_fingerprint",
]
