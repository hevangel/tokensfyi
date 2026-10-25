"""Application service for capability exchange and scoped management sessions.

Orchestrates domain logic, persistence, and infrastructure ports to:
- Issue new capabilities (called during submission confirmation)
- Exchange a presented token for a short-lived management session
- Rotate capabilities safely
- Revoke capabilities
- Produce non-enumerating denial responses for all failure modes
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from token_budget_platform.domain.ports import Clock
from token_budget_platform.domain.submission.capability import (
    CAPABILITY_TOKEN_BYTES,
    DEFAULT_CAPABILITY_LIFETIME,
    DEFAULT_SESSION_LIFETIME,
    CapabilityExchangeResult,
    CapabilityRecord,
    CapabilityScope,
    HmacKeyProvider,
    IssuedCapability,
    IssuedSession,
    compute_session_digest,
    compute_token_digest,
    generate_capability_token,
    generate_session_secret,
    should_rotate_capability,
    validate_capability_for_exchange,
    verify_token_constant_time,
)

# ──── Repository protocol ─────────────────────────────────────────────────────


class CapabilityRepository(Protocol):
    """Persistence boundary for capabilities and sessions."""

    def find_by_digest(self, token_digest: str) -> CapabilityRecord | None:
        """Look up a capability by its HMAC digest. Returns None if not found."""
        ...

    def find_active_for_record(self, record_id: UUID) -> list[CapabilityRecord]:
        """Return all non-revoked, non-expired capabilities for a record."""
        ...

    def save_capability(
        self,
        *,
        record_id: UUID,
        key_version: int,
        token_digest: str,
        scope: CapabilityScope,
        issued_at: datetime,
        expires_at: datetime,
    ) -> UUID:
        """Persist a new capability record. Returns its ID."""
        ...

    def mark_used(self, capability_id: UUID, used_at: datetime) -> None:
        """Record the time a capability was last used."""
        ...

    def revoke(self, capability_id: UUID, revoked_at: datetime) -> None:
        """Mark a capability as revoked."""
        ...

    def save_session(
        self,
        *,
        record_id: UUID,
        session_digest: str,
        expires_at: datetime,
    ) -> UUID:
        """Persist a management session. Returns the session ID."""
        ...

    def find_session_by_digest(self, session_digest: str) -> SessionRecord | None:
        """Look up a session by its digest."""
        ...


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """Stored session representation."""

    id: UUID
    record_id: UUID
    session_digest: str
    expires_at: datetime
    revoked_at: datetime | None


# ──── Exchange result ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExchangeOutcome:
    """Result of a capability exchange attempt.

    On success, contains a session and optional rotated capability token.
    On failure, result indicates the category without revealing existence info.
    """

    success: bool
    session: IssuedSession | None = None
    rotated_token: str | None = None
    denial_reason: str = ""


# ──── Service ─────────────────────────────────────────────────────────────────


class CapabilityService:
    """Coordinates capability issuance, exchange, rotation, and revocation."""

    def __init__(
        self,
        *,
        clock: Clock,
        key_provider: HmacKeyProvider,
        repository: CapabilityRepository,
        capability_lifetime: timedelta = DEFAULT_CAPABILITY_LIFETIME,
        session_lifetime: timedelta = DEFAULT_SESSION_LIFETIME,
    ) -> None:
        self._clock = clock
        self._key_provider = key_provider
        self._repository = repository
        self._capability_lifetime = capability_lifetime
        self._session_lifetime = session_lifetime

    def issue_capability(
        self,
        record_id: UUID,
        scope: CapabilityScope = CapabilityScope.MANAGE_SUBMISSION,
    ) -> IssuedCapability:
        """Issue a new 256-bit capability token for a record.

        Returns the raw token exactly once; only the HMAC digest is persisted.
        """
        now = self._clock.now()
        raw_token = generate_capability_token()
        key_version = self._key_provider.current_key_version()
        key = self._key_provider.get_key(key_version)
        digest = compute_token_digest(raw_token, key, key_version)

        expires_at = now + self._capability_lifetime

        self._repository.save_capability(
            record_id=record_id,
            key_version=key_version,
            token_digest=digest,
            scope=scope,
            issued_at=now,
            expires_at=expires_at,
        )

        return IssuedCapability(
            record_id=record_id,
            raw_token=raw_token,
            key_version=key_version,
            scope=scope,
            issued_at=now,
            expires_at=expires_at,
        )

    def exchange_capability(
        self,
        presented_token: str,
        required_scope: CapabilityScope = CapabilityScope.MANAGE_SUBMISSION,
    ) -> ExchangeOutcome:
        """Exchange a raw capability token for a scoped management session.

        Non-enumerating: all failure modes return the same denial shape.
        On success, marks the capability used, optionally rotates it,
        and issues a short-lived session bound to the record.
        """
        now = self._clock.now()

        # Validate token format: must be valid hex of the expected length
        if not _is_valid_token_format(presented_token):
            return _denial()

        # Compute the digest using the current key version to look up
        key_version = self._key_provider.current_key_version()
        key = self._key_provider.get_key(key_version)
        candidate_digest = compute_token_digest(presented_token, key, key_version)

        # Look up by digest
        capability = self._repository.find_by_digest(candidate_digest)

        # If not found with current key, the token might have been issued with
        # a prior key version. We do a brute-force check across versions.
        # In practice there are very few key versions (1-3).
        if capability is None:
            capability = self._find_across_versions(presented_token)

        if capability is None:
            # Constant-time: still perform a comparison against dummy to prevent
            # timing leakage between "not found" and "found but invalid"
            _dummy_comparison()
            return _denial()

        # Verify the token against the stored digest in constant time
        if not verify_token_constant_time(
            presented_token, capability.token_digest, self._key_provider
        ):
            return _denial()

        # Validate business rules (expiry, revocation, scope)
        result = validate_capability_for_exchange(capability, now, required_scope)
        if result != CapabilityExchangeResult.SUCCESS:
            return _denial()

        # Mark used
        self._repository.mark_used(capability.id, now)

        # Rotate if needed
        rotated_token: str | None = None
        if should_rotate_capability(capability, now):
            rotated = self._rotate_capability(capability)
            rotated_token = rotated.raw_token

        # Issue session
        session = self._issue_session(capability.record_id)

        return ExchangeOutcome(
            success=True,
            session=session,
            rotated_token=rotated_token,
        )

    def revoke_for_record(self, record_id: UUID) -> int:
        """Revoke all active capabilities for a record. Returns count revoked."""
        now = self._clock.now()
        active = self._repository.find_active_for_record(record_id)
        for cap in active:
            self._repository.revoke(cap.id, now)
        return len(active)

    def validate_session(self, session_secret: str) -> SessionRecord | None:
        """Validate a session secret and return the session if active."""
        digest = compute_session_digest(session_secret)
        session = self._repository.find_session_by_digest(digest)
        if session is None:
            return None
        now = self._clock.now()
        if now >= session.expires_at:
            return None
        if session.revoked_at is not None:
            return None
        return session

    # ──── Private helpers ──────────────────────────────────────────────────────

    def _find_across_versions(self, token: str) -> CapabilityRecord | None:
        """Try all known key versions to find a matching capability."""
        current = self._key_provider.current_key_version()
        for version in range(1, current + 1):
            key = self._key_provider.get_key(version)
            digest = compute_token_digest(token, key, version)
            found = self._repository.find_by_digest(digest)
            if found is not None:
                return found
        return None

    def _rotate_capability(self, old: CapabilityRecord) -> IssuedCapability:
        """Revoke the old capability and issue a fresh one for the same record."""
        now = self._clock.now()
        self._repository.revoke(old.id, now)
        return self.issue_capability(old.record_id, old.scope)

    def _issue_session(self, record_id: UUID) -> IssuedSession:
        """Create and persist a short-lived management session."""
        now = self._clock.now()
        session_secret = generate_session_secret()
        session_digest = compute_session_digest(session_secret)
        expires_at = now + self._session_lifetime

        session_id = self._repository.save_session(
            record_id=record_id,
            session_digest=session_digest,
            expires_at=expires_at,
        )

        return IssuedSession(
            session_id=session_id,
            session_secret=session_secret,
            record_id=record_id,
            expires_at=expires_at,
        )


# ──── Module-private helpers ───────────────────────────────────────────────────


def _is_valid_token_format(token: str) -> bool:
    """Check that a token is a hex string of the expected length."""
    if len(token) != CAPABILITY_TOKEN_BYTES * 2:
        return False
    try:
        int(token, 16)
    except ValueError:
        return False
    return True


def _dummy_comparison() -> None:
    """Perform a constant-time comparison against random data to prevent timing leakage."""
    import hmac as _hmac

    dummy_a = secrets.token_bytes(32)
    dummy_b = secrets.token_bytes(32)
    _hmac.compare_digest(dummy_a, dummy_b)


def _denial() -> ExchangeOutcome:
    """Standard non-enumerating denial response."""
    return ExchangeOutcome(
        success=False,
        denial_reason=(
            "The management link is invalid or has expired. "
            "Use the recovery option if you need a new link."
        ),
    )
