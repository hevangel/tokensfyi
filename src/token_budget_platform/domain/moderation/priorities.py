"""Moderation queue priority ordering.

Priority values are lower-is-higher (1 = most urgent).
The queue is ordered by (priority ASC, opened_at ASC) so the most
urgent and oldest cases surface first.

Design doc priority order:
  1. Confirmed/suspected sensitive exposure
  2. Malicious evidence/security events
  3. Disputes
  4. Contributor changes
  5. Verification
  6. Duplicate review
  7. Ordinary submissions
"""

from __future__ import annotations

from enum import IntEnum


class CasePriority(IntEnum):
    """Named priority levels matching the design specification."""

    SENSITIVE_EXPOSURE = 10
    MALICIOUS_EVIDENCE = 20
    DISPUTE = 30
    CONTRIBUTOR_CHANGES = 40
    VERIFICATION = 50
    DUPLICATE_REVIEW = 60
    ORDINARY_SUBMISSION = 70


# Map moderation case sources to default priorities.
_SOURCE_PRIORITY_MAP: dict[str, CasePriority] = {
    "REPORT": CasePriority.DISPUTE,  # Default for reports; sensitive overridden below
    "DISPUTE": CasePriority.DISPUTE,
    "VERIFICATION": CasePriority.VERIFICATION,
    "DUPLICATE": CasePriority.DUPLICATE_REVIEW,
    "SUBMISSION": CasePriority.ORDINARY_SUBMISSION,
}

# Report categories that trigger elevated priority
SENSITIVE_CATEGORIES = frozenset({"PERSONAL_DATA", "SENSITIVE_DATA"})
MALICIOUS_CATEGORIES = frozenset({"MALICIOUS_CONTENT", "SECURITY_EVENT"})


def priority_for_source(source: str, category: str = "") -> int:
    """Determine priority value for a case based on source and category.

    Returns an integer priority value where lower means more urgent.
    """
    if category in SENSITIVE_CATEGORIES:
        return CasePriority.SENSITIVE_EXPOSURE
    if category in MALICIOUS_CATEGORIES:
        return CasePriority.MALICIOUS_EVIDENCE

    return _SOURCE_PRIORITY_MAP.get(source, CasePriority.ORDINARY_SUBMISSION)
