"""Moderation and dispute state machine rules.

Defines allowed publication-state transitions and verification-status
transitions for dispute resolution.

Design doc state machine:
  Disputed -> Unverified (resolved unsupported)
  Disputed -> EvidenceSubmitted (resolved pending evidence)
  Disputed -> Verified (resolved confirmed)
  Disputed -> Rejected (resolved rejected)

The case stores status_before_dispute, and resolution must choose an
allowed resulting status.
"""

from __future__ import annotations


class ModerationTransitionError(ValueError):
    """Raised when a requested state transition is invalid."""

    def __init__(self, from_state: str, to_state: str, context: str = "") -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.context = context
        msg = f"Invalid transition from {from_state} to {to_state}"
        if context:
            msg += f": {context}"
        super().__init__(msg)


# Allowed publication state transitions (from -> set of allowed targets)
ALLOWED_PUBLICATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"QUEUED"}),
    "QUEUED": frozenset({"APPROVED", "REJECTED", "CHANGES_REQUESTED", "WITHDRAWN"}),
    "CHANGES_REQUESTED": frozenset({"QUEUED", "WITHDRAWN"}),
    "APPROVED": frozenset({"QUEUED", "WITHDRAWN"}),
    "REJECTED": frozenset({"QUEUED"}),
}

# Allowed dispute resolution verification status targets.
# From Disputed, resolution can return to any of these states.
ALLOWED_DISPUTE_RESOLUTION_STATUSES: frozenset[str] = frozenset(
    {"UNVERIFIED", "EVIDENCE_SUBMITTED", "VERIFIED", "REJECTED"}
)


def validate_publication_transition(from_state: str, to_state: str) -> None:
    """Raise ModerationTransitionError if the transition is not allowed."""
    allowed = ALLOWED_PUBLICATION_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise ModerationTransitionError(from_state, to_state)


def validate_dispute_resolution_status(target_status: str) -> None:
    """Validate that a dispute resolution targets an allowed verification status.

    Requirement 7.9: Resolution must record the resulting Verification_Status.
    The state machine only allows transitioning from Disputed to:
      - Unverified, Evidence_Submitted, Verified, or Rejected
    """
    if target_status not in ALLOWED_DISPUTE_RESOLUTION_STATUSES:
        raise ModerationTransitionError(
            "DISPUTED",
            target_status,
            context="dispute resolution must target an allowed verification status",
        )
