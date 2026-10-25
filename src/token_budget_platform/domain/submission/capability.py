"""Domain logic for secure capability tokens and scoped management sessions.

Capabilities are 256-bit random tokens issued to contributors after confirmation.
Only versioned HMAC digests are persisted; raw tokens are never stored or logged.
Comparison uses constant-time algorithms to prevent timing side-channels.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

# ──── Constants ────────────────────────────────────────────────────────────────

CAPABILITY_TOKEN_BYTES = 32  # 256 bits
SESSION_SECRET_BYTES = 32  # 256 bits
DEFAULT_CAPABILITY_LIFETIME = timedelta(days=90)
DEFAULT_SESSION_LIFETIME = timedelta(minutes=30)
HMAC_ALGORITHM = "sha256"


class CapabilityScope(StrEnum):
    MANAGE_SUBMISSION = "MANAGE_SUBMISSION"


class CapabilityExchangeResult(StrEnum):
    SUCCESS = "SUCCESS"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"


# ──── Value Objects ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IssuedCapability:
    """Returned to the caller once; the raw_token is the only copy."""

    record_id: UUID
    raw_token: str
    key_version: int
    scope: CapabilityScope
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Short-lived scoped session returned after successful capability exchange."""

    session_id: UUID
    session_secret: str
    record_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    """Stored representation of a capability (digest only, never the token)."""

    id: UUID
    record_id: UUID
    key_version: int
    token_digest: str
    scope: CapabilityScope
    issued_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


# ──── Protocol for HMAC key retrieval ──────────────────────────────────────────


class HmacKeyProvider(Protocol):
    """Supplies versioned HMAC keys for capability hashing."""

    def current_key_version(self) -> int: ...

    def get_key(self, version: int) -> bytes: ...


# ──── Core Functions ───────────────────────────────────────────────────────────


def generate_capability_token() -> str:
    """Generate a cryptographically random 256-bit token as a hex string."""
    return secrets.token_hex(CAPABILITY_TOKEN_BYTES)


def compute_token_digest(token: str, key: bytes, key_version: int) -> str:
    """Compute a versioned HMAC-SHA256 digest of a raw capability token.

    The digest format is ``v{version}:{hex_hmac}`` so key rotation is explicit.
    """
    mac = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"v{key_version}:{mac}"


def verify_token_constant_time(
    token: str, stored_digest: str, key_provider: HmacKeyProvider
) -> bool:
    """Compare a presented token against a stored digest in constant time.

    Extracts the key version from the stored digest format and recomputes.
    Returns False for any format error rather than raising.
    """
    try:
        version_str, expected_mac = stored_digest.split(":", 1)
        key_version = int(version_str[1:])  # strip 'v' prefix
    except (ValueError, IndexError):
        return False

    key = key_provider.get_key(key_version)
    computed_mac = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_mac, expected_mac)


def validate_capability_for_exchange(
    capability: CapabilityRecord,
    now: datetime,
    required_scope: CapabilityScope = CapabilityScope.MANAGE_SUBMISSION,
) -> CapabilityExchangeResult:
    """Check expiry, revocation, and scope for a matched capability."""
    if capability.revoked_at is not None:
        return CapabilityExchangeResult.REVOKED
    if now >= capability.expires_at:
        return CapabilityExchangeResult.EXPIRED
    if capability.scope != required_scope:
        return CapabilityExchangeResult.SCOPE_MISMATCH
    return CapabilityExchangeResult.SUCCESS


def generate_session_secret() -> str:
    """Generate a 256-bit session secret as hex."""
    return secrets.token_hex(SESSION_SECRET_BYTES)


def compute_session_digest(secret: str) -> str:
    """Hash a session secret for storage. Uses SHA-256 since sessions are short-lived."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def should_rotate_capability(capability: CapabilityRecord, now: datetime) -> bool:
    """Determine if a capability should be rotated on use.

    Rotate when:
    - The capability has been used before (single-use rotation)
    - The capability is past its midpoint lifetime
    """
    if capability.used_at is not None:
        return True
    midpoint = capability.issued_at + (capability.expires_at - capability.issued_at) / 2
    return now >= midpoint
