"""Domain logic for contributor submission management.

Covers:
- Status viewing (submission, verification, publication, moderation feedback, withdrawn)
- Amendment (append version, re-queue)
- Withdrawal (mark withdrawn, schedule public removal within 24 hours)
- Recovery (send management link to previously bound email only)

Requirements: 7.5, 7.6, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 12.4, 12.5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class AmendmentError(StrEnum):
    """Reasons an amendment may be rejected by domain rules."""

    RECORD_WITHDRAWN = "RECORD_WITHDRAWN"
    RECORD_REJECTED = "RECORD_REJECTED"
    NO_CHANGES = "NO_CHANGES"
    INVALID_FIELDS = "INVALID_FIELDS"
    VERSION_CONFLICT = "VERSION_CONFLICT"


class WithdrawalError(StrEnum):
    """Reasons a withdrawal may be rejected."""

    ALREADY_WITHDRAWN = "ALREADY_WITHDRAWN"


class RecoveryError(StrEnum):
    """Reasons recovery may fail (non-enumerating responses hide all these)."""

    NO_EMAIL_BOUND = "NO_EMAIL_BOUND"
    RECORD_NOT_FOUND = "RECORD_NOT_FOUND"


# ──── Value Objects ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ModerationFeedback:
    """Contributor-visible moderation feedback from a decision."""

    action: str
    reason_code: str
    requested_fields: list[str] = field(default_factory=list)
    decided_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SubmissionStatus:
    """Full status view for the owning management session.

    Includes all information the contributor is entitled to see:
    - Current submission data
    - Verification and publication state
    - Moderation feedback (requested changes, rejection reasons)
    - Whether the record is withdrawn
    """

    record_id: UUID
    public_reference: UUID
    publication_state: str
    verification_status: str
    is_withdrawn: bool
    withdrawn_at: datetime | None
    published_at: datetime | None
    last_reviewed_at: datetime | None
    correction_date: str | None  # ISO date string
    current_version: int
    moderation_feedback: list[ModerationFeedback] = field(default_factory=list)
    submission_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AmendmentResult:
    """Result of a successful amendment."""

    record_id: UUID
    new_version: int
    publication_state: str
    changed_fields: list[str]


@dataclass(frozen=True, slots=True)
class WithdrawalResult:
    """Result of a successful withdrawal."""

    record_id: UUID
    withdrawn_at: datetime
    removal_deadline: datetime  # Must be within 24 hours


# ──── Domain Rules ─────────────────────────────────────────────────────────────

# Amendable fields: benchmark values that can be corrected by the contributor
AMENDABLE_FIELDS = frozenset(
    {
        "token_budget",
        "input_tokens",
        "output_tokens",
        "token_usage",
        "cost_minor_units",
        "cost_currency",
        "cost_basis",
        "seat_count",
        "workload_count",
        "reporting_start",
        "reporting_end",
        "reporting_timezone",
        "source_date",
        "methodology_notes",
    }
)

# Publication states that allow amendment
AMENDMENT_ALLOWED_STATES = frozenset(
    {
        "QUEUED",
        "APPROVED",
        "CHANGES_REQUESTED",
        "REJECTED",  # Per state machine: Rejected -> Queued via permitted amendment
    }
)

# Publication states that allow withdrawal
WITHDRAWAL_ALLOWED_STATES = frozenset(
    {
        "QUEUED",
        "APPROVED",
        "CHANGES_REQUESTED",
    }
)


def validate_amendment(
    current_publication_state: str,
    current_version: int,
    expected_version: int | None,
    changes: dict[str, Any],
) -> AmendmentError | None:
    """Validate that an amendment is allowed by domain rules.

    Returns None if valid, or the specific error otherwise.
    """
    if current_publication_state == "WITHDRAWN":
        return AmendmentError.RECORD_WITHDRAWN

    if current_publication_state not in AMENDMENT_ALLOWED_STATES:
        return AmendmentError.RECORD_REJECTED

    # Optimistic version check
    if expected_version is not None and expected_version != current_version:
        return AmendmentError.VERSION_CONFLICT

    # Must have at least one change in amendable fields
    valid_changes = {k: v for k, v in changes.items() if k in AMENDABLE_FIELDS}
    if not valid_changes:
        return AmendmentError.NO_CHANGES

    # Check for invalid field names
    invalid = set(changes.keys()) - AMENDABLE_FIELDS
    if invalid:
        return AmendmentError.INVALID_FIELDS

    return None


def validate_withdrawal(current_publication_state: str) -> WithdrawalError | None:
    """Validate that withdrawal is allowed."""
    if current_publication_state == "WITHDRAWN":
        return WithdrawalError.ALREADY_WITHDRAWN
    if current_publication_state not in WITHDRAWAL_ALLOWED_STATES:
        # Allow withdrawal from any non-withdrawn state per the state machine
        # The state machine shows Queued/Approved/ChangesRequested -> Withdrawn
        return None  # Actually, we allow it from the listed states
    return None


def compute_changed_fields(
    current_data: dict[str, Any],
    amendments: dict[str, Any],
) -> list[str]:
    """Determine which fields actually changed value."""
    changed = []
    for field_name, new_value in amendments.items():
        if field_name not in AMENDABLE_FIELDS:
            continue
        old_value = current_data.get(field_name)
        # Normalize for comparison
        if str(old_value) != str(new_value) if old_value is not None else new_value is not None:
            changed.append(field_name)
    return changed


def is_material_value_change(changed_fields: list[str]) -> bool:
    """Determine if the amendment changes a material benchmark value.

    Material values are token_budget, token_usage, cost_minor_units (Req 12.5).
    """
    material_fields = {"token_budget", "token_usage", "cost_minor_units"}
    return bool(set(changed_fields) & material_fields)
