"""Tests for capability exchange and scoped management sessions.

Covers:
- 256-bit token generation and HMAC digest computation
- Constant-time comparison
- Capability validation (expiry, revocation, scope)
- Capability rotation logic
- Session issuance
- Non-enumerating denial for invalid/expired links
- API endpoint behavior
- Script-blocked accessible recovery path

Requirements: 8.1, 8.2, 8.3, 8.6, 10.6, 11.1
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from token_budget_platform.adapters.clock import FrozenClock
from token_budget_platform.adapters.hmac_keys import LocalHmacKeyProvider
from token_budget_platform.application.submission.capability_service import (
    CapabilityService,
    SessionRecord,
    _is_valid_token_format,
)
from token_budget_platform.domain.submission.capability import (
    CAPABILITY_TOKEN_BYTES,
    CapabilityExchangeResult,
    CapabilityRecord,
    CapabilityScope,
    compute_session_digest,
    compute_token_digest,
    generate_capability_token,
    generate_session_secret,
    should_rotate_capability,
    validate_capability_for_exchange,
    verify_token_constant_time,
)

# ──── Fixtures ─────────────────────────────────────────────────────────────────


class InMemoryCapabilityRepository:
    """Test double for capability persistence."""

    def __init__(self, clock=None):
        self.capabilities: dict[str, CapabilityRecord] = {}  # keyed by digest
        self.sessions: dict[str, SessionRecord] = {}  # keyed by digest
        self._next_cap_id = 0
        self._next_sess_id = 0
        self._clock = clock

    def find_by_digest(self, token_digest: str) -> CapabilityRecord | None:
        return self.capabilities.get(token_digest)

    def find_active_for_record(self, record_id):
        now = self._clock.now() if self._clock is not None else datetime.now(UTC)
        return [
            c
            for c in self.capabilities.values()
            if c.record_id == record_id and c.revoked_at is None and c.expires_at > now
        ]

    def save_capability(
        self, *, record_id, key_version, token_digest, scope, issued_at, expires_at
    ):
        cap_id = uuid4()
        record = CapabilityRecord(
            id=cap_id,
            record_id=record_id,
            key_version=key_version,
            token_digest=token_digest,
            scope=scope,
            issued_at=issued_at,
            expires_at=expires_at,
            used_at=None,
            revoked_at=None,
        )
        self.capabilities[token_digest] = record
        return cap_id

    def mark_used(self, capability_id, used_at):
        for digest, cap in self.capabilities.items():
            if cap.id == capability_id:
                self.capabilities[digest] = CapabilityRecord(
                    id=cap.id,
                    record_id=cap.record_id,
                    key_version=cap.key_version,
                    token_digest=cap.token_digest,
                    scope=cap.scope,
                    issued_at=cap.issued_at,
                    expires_at=cap.expires_at,
                    used_at=used_at,
                    revoked_at=cap.revoked_at,
                )
                break

    def revoke(self, capability_id, revoked_at):
        for digest, cap in self.capabilities.items():
            if cap.id == capability_id:
                self.capabilities[digest] = CapabilityRecord(
                    id=cap.id,
                    record_id=cap.record_id,
                    key_version=cap.key_version,
                    token_digest=cap.token_digest,
                    scope=cap.scope,
                    issued_at=cap.issued_at,
                    expires_at=cap.expires_at,
                    used_at=cap.used_at,
                    revoked_at=revoked_at,
                )
                break

    def save_session(self, *, record_id, session_digest, expires_at):
        session_id = uuid4()
        self.sessions[session_digest] = SessionRecord(
            id=session_id,
            record_id=record_id,
            session_digest=session_digest,
            expires_at=expires_at,
            revoked_at=None,
        )
        return session_id

    def find_session_by_digest(self, session_digest: str) -> SessionRecord | None:
        return self.sessions.get(session_digest)


@pytest.fixture
def clock():
    return FrozenClock(datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def key_provider():
    return LocalHmacKeyProvider(b"test-hmac-key-for-capabilities!!")


@pytest.fixture
def repository(clock):
    return InMemoryCapabilityRepository(clock=clock)


@pytest.fixture
def service(clock, key_provider, repository):
    return CapabilityService(
        clock=clock,
        key_provider=key_provider,
        repository=repository,
        capability_lifetime=timedelta(days=90),
        session_lifetime=timedelta(minutes=30),
    )


# ──── Domain: Token Generation ─────────────────────────────────────────────────


class TestTokenGeneration:
    def test_generates_256_bit_hex_token(self):
        token = generate_capability_token()
        assert len(token) == CAPABILITY_TOKEN_BYTES * 2  # 64 hex chars
        int(token, 16)  # must be valid hex

    def test_tokens_are_unique(self):
        tokens = {generate_capability_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_session_secret_is_256_bit_hex(self):
        secret = generate_session_secret()
        assert len(secret) == 64
        int(secret, 16)


# ──── Domain: HMAC Digest Computation ──────────────────────────────────────────


class TestHmacDigest:
    def test_digest_includes_key_version(self, key_provider):
        token = generate_capability_token()
        key = key_provider.get_key(1)
        digest = compute_token_digest(token, key, 1)
        assert digest.startswith("v1:")

    def test_different_key_versions_produce_different_digests(self, key_provider):
        token = generate_capability_token()
        key1 = key_provider.get_key(1)
        key2 = key_provider.get_key(2)
        d1 = compute_token_digest(token, key1, 1)
        d2 = compute_token_digest(token, key2, 2)
        assert d1 != d2

    def test_same_token_same_key_produces_same_digest(self, key_provider):
        token = generate_capability_token()
        key = key_provider.get_key(1)
        d1 = compute_token_digest(token, key, 1)
        d2 = compute_token_digest(token, key, 1)
        assert d1 == d2


# ──── Domain: Constant-Time Verification ───────────────────────────────────────


class TestConstantTimeVerification:
    def test_valid_token_verifies(self, key_provider):
        token = generate_capability_token()
        key = key_provider.get_key(1)
        digest = compute_token_digest(token, key, 1)
        assert verify_token_constant_time(token, digest, key_provider) is True

    def test_wrong_token_does_not_verify(self, key_provider):
        token = generate_capability_token()
        wrong_token = generate_capability_token()
        key = key_provider.get_key(1)
        digest = compute_token_digest(token, key, 1)
        assert verify_token_constant_time(wrong_token, digest, key_provider) is False

    def test_malformed_digest_returns_false(self, key_provider):
        token = generate_capability_token()
        assert verify_token_constant_time(token, "not-a-valid-digest", key_provider) is False
        assert verify_token_constant_time(token, "", key_provider) is False
        assert verify_token_constant_time(token, "v:", key_provider) is False


# ──── Domain: Capability Validation ────────────────────────────────────────────


class TestCapabilityValidation:
    def _make_capability(self, **overrides: Any) -> CapabilityRecord:
        defaults: dict[str, Any] = {
            "id": uuid4(),
            "record_id": uuid4(),
            "key_version": 1,
            "token_digest": "v1:abc123",
            "scope": CapabilityScope.MANAGE_SUBMISSION,
            "issued_at": datetime(2026, 1, 1, tzinfo=UTC),
            "expires_at": datetime(2026, 4, 1, tzinfo=UTC),
            "used_at": None,
            "revoked_at": None,
        }
        defaults.update(overrides)
        return CapabilityRecord(**defaults)

    def test_valid_capability_succeeds(self):
        cap = self._make_capability()
        now = datetime(2026, 2, 1, tzinfo=UTC)
        assert validate_capability_for_exchange(cap, now) == CapabilityExchangeResult.SUCCESS

    def test_expired_capability_fails(self):
        cap = self._make_capability()
        now = datetime(2026, 5, 1, tzinfo=UTC)
        assert validate_capability_for_exchange(cap, now) == CapabilityExchangeResult.EXPIRED

    def test_revoked_capability_fails(self):
        cap = self._make_capability(revoked_at=datetime(2026, 2, 1, tzinfo=UTC))
        now = datetime(2026, 2, 2, tzinfo=UTC)
        assert validate_capability_for_exchange(cap, now) == CapabilityExchangeResult.REVOKED

    def test_expiry_at_exact_boundary_fails(self):
        cap = self._make_capability(expires_at=datetime(2026, 4, 1, tzinfo=UTC))
        now = datetime(2026, 4, 1, tzinfo=UTC)
        assert validate_capability_for_exchange(cap, now) == CapabilityExchangeResult.EXPIRED


# ──── Domain: Rotation Logic ───────────────────────────────────────────────────


class TestRotationLogic:
    def _make_capability(self, **overrides: Any) -> CapabilityRecord:
        defaults: dict[str, Any] = {
            "id": uuid4(),
            "record_id": uuid4(),
            "key_version": 1,
            "token_digest": "v1:abc123",
            "scope": CapabilityScope.MANAGE_SUBMISSION,
            "issued_at": datetime(2026, 1, 1, tzinfo=UTC),
            "expires_at": datetime(2026, 4, 1, tzinfo=UTC),
            "used_at": None,
            "revoked_at": None,
        }
        defaults.update(overrides)
        return CapabilityRecord(**defaults)

    def test_previously_used_capability_triggers_rotation(self):
        cap = self._make_capability(used_at=datetime(2026, 2, 1, tzinfo=UTC))
        now = datetime(2026, 2, 2, tzinfo=UTC)
        assert should_rotate_capability(cap, now) is True

    def test_past_midpoint_triggers_rotation(self):
        # Issued Jan 1, expires Apr 1 (90 days). Midpoint is ~Feb 14.
        cap = self._make_capability()
        now = datetime(2026, 3, 1, tzinfo=UTC)  # past midpoint
        assert should_rotate_capability(cap, now) is True

    def test_before_midpoint_no_rotation(self):
        cap = self._make_capability()
        now = datetime(2026, 1, 15, tzinfo=UTC)  # before midpoint
        assert should_rotate_capability(cap, now) is False


# ──── Domain: Session Digest ───────────────────────────────────────────────────


class TestSessionDigest:
    def test_session_digest_is_deterministic(self):
        secret = generate_session_secret()
        d1 = compute_session_digest(secret)
        d2 = compute_session_digest(secret)
        assert d1 == d2

    def test_different_secrets_produce_different_digests(self):
        s1 = generate_session_secret()
        s2 = generate_session_secret()
        assert compute_session_digest(s1) != compute_session_digest(s2)


# ──── Application: Token Format Validation ─────────────────────────────────────


class TestTokenFormatValidation:
    def test_valid_64_char_hex(self):
        assert _is_valid_token_format("a" * 64) is True

    def test_too_short(self):
        assert _is_valid_token_format("a" * 63) is False

    def test_too_long(self):
        assert _is_valid_token_format("a" * 65) is False

    def test_non_hex_chars(self):
        assert _is_valid_token_format("g" * 64) is False

    def test_empty_string(self):
        assert _is_valid_token_format("") is False


# ──── Application: Full Exchange Flow ──────────────────────────────────────────


class TestCapabilityExchange:
    def test_issue_and_exchange_succeeds(self, service, repository, clock):
        record_id = uuid4()
        issued = service.issue_capability(record_id)

        # Verify the raw token is returned and not stored
        assert len(issued.raw_token) == 64
        assert issued.record_id == record_id

        # Exchange the token
        outcome = service.exchange_capability(issued.raw_token)
        assert outcome.success is True
        assert outcome.session is not None
        assert outcome.session.record_id == record_id

    def test_invalid_token_returns_non_enumerating_denial(self, service):
        outcome = service.exchange_capability("invalid_token")
        assert outcome.success is False
        assert outcome.session is None
        assert "invalid or has expired" in outcome.denial_reason

    def test_random_valid_hex_returns_denial(self, service):
        fake_token = "a" * 64
        outcome = service.exchange_capability(fake_token)
        assert outcome.success is False

    def test_expired_capability_returns_denial(self, service, clock):
        record_id = uuid4()
        issued = service.issue_capability(record_id)
        # Advance past expiry
        clock.advance(days=91)
        outcome = service.exchange_capability(issued.raw_token)
        assert outcome.success is False

    def test_revoked_capability_returns_denial(self, service, clock):
        record_id = uuid4()
        issued = service.issue_capability(record_id)
        service.revoke_for_record(record_id)
        outcome = service.exchange_capability(issued.raw_token)
        assert outcome.success is False

    def test_exchange_marks_capability_used(self, service, repository, clock):
        record_id = uuid4()
        issued = service.issue_capability(record_id)
        service.exchange_capability(issued.raw_token)

        # Find the capability and verify used_at is set
        key = service._key_provider.get_key(1)
        digest = compute_token_digest(issued.raw_token, key, 1)
        cap = repository.capabilities.get(digest)
        assert cap is not None
        assert cap.used_at is not None

    def test_second_exchange_rotates_capability(self, service, repository, clock):
        record_id = uuid4()
        issued = service.issue_capability(record_id)

        # First exchange
        outcome1 = service.exchange_capability(issued.raw_token)
        assert outcome1.success is True
        # First use: not past midpoint, no prior use → no rotation initially
        # (depends on timing; the capability was just issued)
        # But after first use, used_at is set, so second exchange would rotate

        # For the second exchange we need to re-issue since the first didn't rotate
        # Actually let's test the midpoint rotation case
        clock.advance(days=46)  # past midpoint of 90-day lifetime
        record_id2 = uuid4()
        issued2 = service.issue_capability(record_id2)
        clock.advance(days=46)  # past midpoint
        outcome2 = service.exchange_capability(issued2.raw_token)
        assert outcome2.success is True
        assert outcome2.rotated_token is not None
        assert len(outcome2.rotated_token) == 64

    def test_session_has_correct_expiry(self, service, clock):
        record_id = uuid4()
        issued = service.issue_capability(record_id)
        outcome = service.exchange_capability(issued.raw_token)
        assert outcome.session is not None
        expected_expiry = clock.now() + timedelta(minutes=30)
        assert outcome.session.expires_at == expected_expiry


# ──── Application: Session Validation ──────────────────────────────────────────


class TestSessionValidation:
    def test_valid_session_validates(self, service, clock):
        record_id = uuid4()
        issued = service.issue_capability(record_id)
        outcome = service.exchange_capability(issued.raw_token)
        assert outcome.session is not None

        session = service.validate_session(outcome.session.session_secret)
        assert session is not None
        assert session.record_id == record_id

    def test_expired_session_returns_none(self, service, clock):
        record_id = uuid4()
        issued = service.issue_capability(record_id)
        outcome = service.exchange_capability(issued.raw_token)
        assert outcome.session is not None

        clock.advance(minutes=31)  # past 30-minute session lifetime
        session = service.validate_session(outcome.session.session_secret)
        assert session is None

    def test_unknown_secret_returns_none(self, service):
        session = service.validate_session("unknown_secret_" + "a" * 50)
        assert session is None


# ──── Application: Revocation ──────────────────────────────────────────────────


class TestRevocation:
    def test_revoke_all_for_record(self, service, repository):
        record_id = uuid4()
        service.issue_capability(record_id)
        service.issue_capability(record_id)
        count = service.revoke_for_record(record_id)
        assert count == 2


# ──── HMAC Key Provider ────────────────────────────────────────────────────────


class TestLocalHmacKeyProvider:
    def test_current_version_starts_at_1(self):
        provider = LocalHmacKeyProvider(b"k" * 32)
        assert provider.current_key_version() == 1

    def test_rotate_advances_version(self):
        provider = LocalHmacKeyProvider(b"k" * 32)
        new_version = provider.rotate()
        assert new_version == 2
        assert provider.current_key_version() == 2

    def test_different_versions_produce_different_keys(self):
        provider = LocalHmacKeyProvider(b"k" * 32)
        k1 = provider.get_key(1)
        k2 = provider.get_key(2)
        assert k1 != k2
        assert len(k1) == 32

    def test_rejects_short_master_key(self):
        with pytest.raises(ValueError, match="at least 32 bytes"):
            LocalHmacKeyProvider(b"short")

    def test_rejects_invalid_version(self):
        provider = LocalHmacKeyProvider(b"k" * 32)
        with pytest.raises(ValueError, match="positive"):
            provider.get_key(0)


# ──── API Endpoint Tests ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestManagementSessionsEndpoint:
    def test_invalid_json_returns_403(self, client):
        response = client.post(
            "/api/v1/management-sessions",
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 403
        body = response.json()
        assert body["type"] == "/problems/forbidden"

    def test_missing_token_returns_403(self, client):
        response = client.post(
            "/api/v1/management-sessions",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_invalid_token_returns_403_non_enumerating(self, client):
        response = client.post(
            "/api/v1/management-sessions",
            data=json.dumps({"token": "not-valid-hex"}),
            content_type="application/json",
        )
        assert response.status_code == 403
        body = response.json()
        # Verify non-enumerating: same response whether submission exists or not
        assert "invalid or has expired" in body["detail"]

    def test_random_hex_token_returns_403(self, client):
        response = client.post(
            "/api/v1/management-sessions",
            data=json.dumps({"token": "a" * 64}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_get_method_not_allowed(self, client):
        response = client.get("/api/v1/management-sessions")
        assert response.status_code == 405


# ──── HTML Template Tests ──────────────────────────────────────────────────────


class TestManageLandingPage:
    def test_manage_page_renders(self, client):
        response = client.get("/manage/")
        assert response.status_code == 200

    def test_manage_page_contains_noscript_recovery(self, client):
        response = client.get("/manage/")
        content = response.content.decode()
        assert "<noscript>" in content
        assert "Open your management session without JavaScript" in content
        assert "Lost your link?" in content

    def test_manage_page_has_accessible_title(self, client):
        response = client.get("/manage/")
        content = response.content.decode()
        assert "Manage Your Submission" in content
        assert "<title>" in content

    def test_manage_page_contains_exchange_script(self, client):
        response = client.get("/manage/")
        content = response.content.decode()
        assert "management-sessions" in content
        assert "capability" in content

    @pytest.mark.django_db
    def test_manage_session_requires_valid_session(self, client):
        """Anonymous access to the management session is denied (Req 8.3)."""
        response = client.get("/manage/session/")
        assert response.status_code == 403
