"""Moderation decision value objects and commands.

Requirement 7.9: Resolution records decision, reason, timestamp,
and resulting Verification_Status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DecisionAction(StrEnum):
    """All possible moderation decision actions."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    RESOLVE_DISPUTE = "RESOLVE_DISPUTE"
    DISMISS = "DISMISS"
    SUPPRESS = "SUPPRESS"


class PublicationDecision(StrEnum):
    """Publication state outcomes from moderation decisions."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    QUEUED = "QUEUED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


@dataclass(frozen=True, slots=True)
class ModerationDecisionRequest:
    """Command to record a moderation decision on a case.

    Contains all metadata required by Requirement 7.9 for traceable resolution.
    """

    case_id: UUID
    action: DecisionAction
    reason_code: str
    rationale: str  # Free-text rationale (encrypted at persistence)
    staff_id: UUID
    resulting_publication_state: str
    resulting_verification_status: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class ModerationDecisionResult:
    """Outcome of a moderation decision attempt."""

    success: bool
    decision_id: UUID | None = None
    error: str = ""
