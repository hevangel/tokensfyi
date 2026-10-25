"""Source-scoped rate limiting domain logic.

Uses keyed network-source pseudonyms (not raw IP addresses) and deterministic
time windows to enforce per-source request limits on submissions and community
reports.  Other sources remain unaffected when one exceeds its limit.

Requirement 10.10:
  IF one network source exceeds the configured Rate_Limit for submissions or
  Community_Reports, THEN THE Token_Budget_Platform SHALL reject excess requests
  with a positive retry interval consistent with the configured window.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Configuration for a single rate-limited endpoint scope."""

    max_requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if self.window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a rate limit check."""

    allowed: bool
    retry_after: int  # seconds until window resets; 0 when allowed


def compute_network_pseudonym(raw_source: str, *, key: bytes) -> str:
    """Derive a keyed pseudonym from a network source identifier.

    Uses HMAC-SHA256 with a secret key so that:
    - The raw IP/source is never stored or logged.
    - The pseudonym is deterministic per (key, source) pair.
    - Pseudonym rotation is possible by changing the key.
    """
    digest = hmac.HMAC(key, raw_source.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"nps:{digest[:32]}"


def compute_window_start(now: datetime, window_seconds: int) -> datetime:
    """Return the deterministic start of the current window.

    Windows are aligned to UTC epoch so that all processes agree on boundaries
    without coordination.
    """
    epoch_seconds = int(now.timestamp())
    window_start_epoch = (epoch_seconds // window_seconds) * window_seconds
    return datetime.fromtimestamp(window_start_epoch, tz=UTC)


def compute_retry_after(now: datetime, window_seconds: int) -> int:
    """Return the positive number of seconds until the current window expires."""
    epoch_seconds = int(now.timestamp())
    window_start_epoch = (epoch_seconds // window_seconds) * window_seconds
    window_end_epoch = window_start_epoch + window_seconds
    remaining = window_end_epoch - epoch_seconds
    # Ensure positive; if exactly at boundary, return full window
    return max(remaining, 1)


class RateLimitStore(Protocol):
    """Port for persisting and querying rate limit counters."""

    def increment_and_check(
        self,
        *,
        pseudonym: str,
        scope: str,
        window_start: datetime,
        window_seconds: int,
        max_requests: int,
    ) -> RateLimitResult:
        """Atomically increment the counter and return whether the request is allowed.

        Returns RateLimitResult with allowed=True if the count (after increment)
        is within max_requests, otherwise allowed=False with positive retry_after.
        """
        ...
