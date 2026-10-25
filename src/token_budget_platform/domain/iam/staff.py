"""Staff identity value objects and lockout policy.

Lockout rule (Requirement 10.5):
  Five failed authentication attempts targeting one privileged account
  within 15 minutes suspends further attempts for that account for 15 minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class StaffRole(StrEnum):
    MODERATOR = "MODERATOR"
    ADMINISTRATOR = "ADMINISTRATOR"


LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW = timedelta(minutes=15)
LOCKOUT_DURATION = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class StaffIdentity:
    """Immutable snapshot of a staff user for authentication/authorization decisions."""

    user_id: str
    username: str
    role: StaffRole
    is_active: bool
    session_revocation_epoch: int


@dataclass(slots=True)
class StaffCredential:
    """Mutable credential state used during authentication flows."""

    password_hash: str
    encrypted_mfa_secret: bytes
    recovery_code_hashes: list[str]
    failed_attempt_window_start: datetime | None
    failed_attempt_count: int
    locked_until: datetime | None


@dataclass(frozen=True, slots=True)
class LockoutPolicy:
    """Exact five-failures-in-15-minutes lockout rule as specified."""

    threshold: int = LOCKOUT_THRESHOLD
    window: timedelta = LOCKOUT_WINDOW
    duration: timedelta = LOCKOUT_DURATION

    def is_locked(self, credential: StaffCredential, now: datetime) -> bool:
        """Return True if the account is currently locked out."""
        if credential.locked_until is not None and now < credential.locked_until:
            return True
        return False

    def record_failure(self, credential: StaffCredential, now: datetime) -> bool:
        """Record a failed attempt and return True if lockout was triggered.

        The window resets if the previous window has expired.
        """
        # If currently locked, don't change anything - the account stays locked
        if self.is_locked(credential, now):
            return True

        # If the window has expired (or never started), start a new window
        if (
            credential.failed_attempt_window_start is None
            or now >= credential.failed_attempt_window_start + self.window
        ):
            credential.failed_attempt_window_start = now
            credential.failed_attempt_count = 1
            return False

        # Within the existing window - increment
        credential.failed_attempt_count += 1
        if credential.failed_attempt_count >= self.threshold:
            credential.locked_until = now + self.duration
            return True

        return False

    def clear_failures(self, credential: StaffCredential) -> None:
        """Reset failure state on successful authentication."""
        credential.failed_attempt_window_start = None
        credential.failed_attempt_count = 0
        credential.locked_until = None
