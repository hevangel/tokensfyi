"""Public eligibility rules for token budget records.

A record is publicly eligible if and only if:
  publication_state == APPROVED AND withdrawn_at IS NULL AND suppressed_at IS NULL

Requirement 7.3: Approval makes a record publicly eligible only when neither
withdrawal nor suppression flags are present.
Requirement 7.4: Rejection excludes from all public repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PublicEligibility:
    """Evaluated eligibility for public display."""

    eligible: bool
    publication_state: str
    withdrawn_at: datetime | None = None
    suppressed_at: datetime | None = None
    reason: str = ""


def is_publicly_eligible(
    publication_state: str,
    withdrawn_at: datetime | None,
    suppressed_at: datetime | None,
) -> PublicEligibility:
    """Evaluate whether a record is publicly eligible.

    Public eligibility requires:
      - publication_state = APPROVED
      - withdrawn_at is None
      - suppressed_at is None
    """
    if publication_state != "APPROVED":
        return PublicEligibility(
            eligible=False,
            publication_state=publication_state,
            withdrawn_at=withdrawn_at,
            suppressed_at=suppressed_at,
            reason=f"publication_state_is_{publication_state}",
        )
    if withdrawn_at is not None:
        return PublicEligibility(
            eligible=False,
            publication_state=publication_state,
            withdrawn_at=withdrawn_at,
            suppressed_at=suppressed_at,
            reason="record_is_withdrawn",
        )
    if suppressed_at is not None:
        return PublicEligibility(
            eligible=False,
            publication_state=publication_state,
            withdrawn_at=withdrawn_at,
            suppressed_at=suppressed_at,
            reason="record_is_suppressed",
        )
    return PublicEligibility(
        eligible=True,
        publication_state=publication_state,
        withdrawn_at=withdrawn_at,
        suppressed_at=suppressed_at,
    )
