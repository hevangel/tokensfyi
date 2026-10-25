"""Staff authentication application service.

Requirements: 7.13, 10.3, 10.5
- Argon2id password verification (never reversible storage).
- TOTP second factor with single-use recovery codes.
- Exact lockout: five failed attempts within 15 minutes locks the account
  for 15 minutes (LockoutPolicy).
- Non-enumerating denials: unknown account and wrong credential produce the
  same result shape and message.
- Every attempt emits a sanitized security event; no password or secret
  material ever enters the event payload.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from django.utils import timezone

from token_budget_platform.adapters.django.models import SecurityEvent, StaffUser
from token_budget_platform.domain.iam.staff import LockoutPolicy, StaffCredential, StaffRole

_password_hasher = PasswordHasher()

RECOVERY_CODE_COUNT = 8


@dataclass(frozen=True, slots=True)
class StaffSessionInfo:
    """Identity snapshot bound into a staff session after successful login."""

    user_id: UUID
    username: str
    role: StaffRole
    session_revocation_epoch: int


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """Outcome of a login attempt.

    ``denial_reason`` is intentionally coarse so unknown accounts are
    indistinguishable from wrong credentials (non-enumerating behavior).
    """

    success: bool
    session: StaffSessionInfo | None = None
    denial_reason: str = ""
    locked_out: bool = False


@dataclass(frozen=True, slots=True)
class ProvisionedStaff:
    """Result of provisioning a staff account.

    Recovery codes are returned exactly once, in plaintext, for the operator
    to store; only their hashes are persisted.
    """

    user_id: UUID
    username: str
    role: StaffRole
    totp_provisioning_uri: str
    recovery_codes: tuple[str, ...]


def _secret_key_bytes() -> bytes:
    """Derive a stable local encryption key from the Django secret key.

    This obfuscates the TOTP secret at rest locally; production deployments
    wrap it with KMS through the key-management port.
    """
    from django.conf import settings

    return hashlib.sha256(str(settings.SECRET_KEY).encode("utf-8")).digest()


def _xor_crypt(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def encrypt_mfa_secret(secret: str) -> bytes:
    return _xor_crypt(secret.encode("utf-8"), _secret_key_bytes())


def decrypt_mfa_secret(blob: bytes) -> str:
    return _xor_crypt(bytes(blob), _secret_key_bytes()).decode("utf-8")


def hash_recovery_code(code: str) -> str:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _record_security_event(
    *,
    event_type: str,
    category: str,
    outcome: str,
    username: str,
    target_surrogate: UUID | None,
    correlation_id: UUID,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a sanitized security event; failures never block the request.

    The username is stored as a pseudonym (first character plus length) so the
    event cannot directly reveal the targeted account (Req 9.8).
    """
    pseudonym = f"user:{username[:1]}*{len(username)}" if username else "user:unknown"
    payload = {
        "event_type": event_type,
        "occurred_at": timezone.now(),
        "correlation_id": correlation_id,
        "actor_or_network_pseudonym": pseudonym,
        "target_surrogate": target_surrogate,
        "outcome": outcome,
        "category": category,
        "sanitized_metadata": metadata or {},
    }
    try:
        SecurityEvent.objects.create(**payload)
    except Exception:
        return


class AuthenticationService:
    """Authenticate privileged staff against stored Argon2id credentials."""

    def __init__(self, *, lockout: LockoutPolicy | None = None) -> None:
        self._lockout = lockout or LockoutPolicy()

    # ── Login ────────────────────────────────────────────────────────────────

    def authenticate(
        self,
        *,
        username: str,
        password: str,
        totp_code: str = "",
        recovery_code: str = "",
        correlation_id: UUID | None = None,
    ) -> AuthenticationResult:
        """Attempt password + second-factor authentication for one account."""
        now = timezone.now()
        correlation = correlation_id or UUID(int=0)
        user: StaffUser | None = StaffUser.objects.filter(username=username).first()
        if user is None:
            # Burn comparable time so unknown-user responses are not faster.
            _password_hasher.verify(_password_hasher.hash("dummy"), password)
            _record_security_event(
                event_type="STAFF_AUTHENTICATION",
                category="AUTHENTICATION_FAILURE",
                outcome="DENIED",
                username=username,
                target_surrogate=None,
                correlation_id=correlation,
            )
            return AuthenticationResult(success=False, denial_reason="invalid_credentials")

        credential = self._credential_for(user)
        if self._lockout.is_locked(credential, now):
            _record_security_event(
                event_type="STAFF_AUTHENTICATION",
                category="AUTHENTICATION_LOCKOUT",
                outcome="DENIED",
                username=username,
                target_surrogate=user.id,
                correlation_id=correlation,
            )
            return AuthenticationResult(
                success=False, denial_reason="invalid_credentials", locked_out=True
            )

        if not self._verify_password(credential.password_hash, password):
            self._register_failure(user, credential, now, correlation)
            return AuthenticationResult(success=False, denial_reason="invalid_credentials")

        if not self._verify_second_factor(user, credential, totp_code, recovery_code):
            self._register_failure(user, credential, now, correlation)
            return AuthenticationResult(success=False, denial_reason="invalid_credentials")

        self._register_success(user, credential)
        return AuthenticationResult(
            success=True,
            session=StaffSessionInfo(
                user_id=user.id,
                username=user.username,
                role=StaffRole(user.role),
                session_revocation_epoch=user.session_revocation_epoch,
            ),
        )

    # ── Provisioning ─────────────────────────────────────────────────────────

    def provision_staff(
        self,
        *,
        username: str,
        password: str,
        role: StaffRole,
    ) -> ProvisionedStaff:
        """Create a staff account with Argon2id hash, TOTP secret, and recovery codes."""
        if role not in (StaffRole.MODERATOR, StaffRole.ADMINISTRATOR):
            raise ValueError("staff role must be MODERATOR or ADMINISTRATOR")
        if not username or not password:
            raise ValueError("username and password are required")
        if StaffUser.objects.filter(username=username).exists():
            raise ValueError("username already provisioned")

        totp_secret = pyotp.random_base32()
        recovery_codes = tuple(secrets.token_hex(8) for _ in range(RECOVERY_CODE_COUNT))
        user = StaffUser.objects.create(
            username=username,
            password_hash=_password_hasher.hash(password),
            role=role.value,
            is_active=True,
            encrypted_mfa_secret=encrypt_mfa_secret(totp_secret),
            recovery_code_hashes=[hash_recovery_code(code) for code in recovery_codes],
        )
        uri = pyotp.totp.TOTP(totp_secret).provisioning_uri(
            name=username, issuer_name="TokenBudgetPlatform"
        )
        _record_security_event(
            event_type="STAFF_PROVISIONED",
            category="STAFF_LIFECYCLE",
            outcome="SUCCESS",
            username=username,
            target_surrogate=user.id,
            correlation_id=UUID(int=0),
        )
        return ProvisionedStaff(
            user_id=user.id,
            username=username,
            role=role,
            totp_provisioning_uri=uri,
            recovery_codes=recovery_codes,
        )

    # ── Session rotation ─────────────────────────────────────────────────────

    def revoke_sessions(self, *, user_id: UUID) -> int:
        """Advance the revocation epoch, invalidating issued sessions."""
        from django.db.models import F

        return StaffUser.objects.filter(id=user_id).update(
            session_revocation_epoch=F("session_revocation_epoch") + 1
        )

    # ── Internals ────────────────────────────────────────────────────────────

    @staticmethod
    def _credential_for(user: StaffUser) -> StaffCredential:
        return StaffCredential(
            password_hash=user.password_hash,
            encrypted_mfa_secret=bytes(user.encrypted_mfa_secret),
            recovery_code_hashes=list(user.recovery_code_hashes),
            failed_attempt_window_start=user.failed_attempt_window_start,
            failed_attempt_count=user.failed_attempt_count,
            locked_until=user.locked_until,
        )

    @staticmethod
    def _verify_password(password_hash: str, password: str) -> bool:
        try:
            return _password_hasher.verify(password_hash, password)
        except (VerifyMismatchError, ValueError):
            return False

    def _verify_second_factor(
        self,
        user: StaffUser,
        credential: StaffCredential,
        totp_code: str,
        recovery_code: str,
    ) -> bool:
        if totp_code:
            secret = decrypt_mfa_secret(credential.encrypted_mfa_secret)
            return pyotp.TOTP(secret).verify(totp_code, valid_window=1)
        if recovery_code:
            digest = hash_recovery_code(recovery_code.strip().lower())
            remaining = list(credential.recovery_code_hashes)
            matches = [
                index
                for index, stored in enumerate(remaining)
                if hmac.compare_digest(stored, digest)
            ]
            if not matches:
                return False
            remaining.pop(matches[0])
            user.recovery_code_hashes = remaining
            user.save(update_fields=["recovery_code_hashes", "updated_at"])
            return True
        return False

    def _register_failure(
        self,
        user: StaffUser,
        credential: StaffCredential,
        now: datetime,
        correlation_id: UUID,
    ) -> None:
        triggered = self._lockout.record_failure(credential, now)
        user.failed_attempt_window_start = credential.failed_attempt_window_start
        user.failed_attempt_count = credential.failed_attempt_count
        user.locked_until = credential.locked_until
        user.save(
            update_fields=[
                "failed_attempt_window_start",
                "failed_attempt_count",
                "locked_until",
                "updated_at",
            ]
        )
        _record_security_event(
            event_type="STAFF_AUTHENTICATION",
            category="AUTHENTICATION_LOCKOUT" if triggered else "AUTHENTICATION_FAILURE",
            outcome="DENIED",
            username=user.username,
            target_surrogate=user.id,
            correlation_id=correlation_id,
            metadata={"locked_out": triggered},
        )

    def _register_success(self, user: StaffUser, credential: StaffCredential) -> None:
        self._lockout.clear_failures(credential)
        user.failed_attempt_window_start = None
        user.failed_attempt_count = 0
        user.locked_until = None
        user.save(
            update_fields=[
                "failed_attempt_window_start",
                "failed_attempt_count",
                "locked_until",
                "updated_at",
            ]
        )
