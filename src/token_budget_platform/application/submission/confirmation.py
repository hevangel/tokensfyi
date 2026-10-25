"""Atomic, idempotent submission confirmation (Req 5.7, 5.8, 7.1, 8.1).

One confirmation transaction creates, all-or-nothing:
- one Unverified TokenBudgetRecord in QUEUED publication state,
- the immutable initial RecordVersion,
- the SUBMISSION moderation queue case,
- the hashed 256-bit management capability (raw token returned exactly once),
- optional encrypted contributor contact binding,
- outbox events for normalization and moderation-queue notification,
- an audit append attempt registered on commit.

Replays of the same (requester scope, idempotency key) return the original
outcome without creating anything new (Req 7.1).  The preview shown before
confirmation is generated through the same public projection used after
publication, so the contributor confirms exactly what would be published
(Req 5.8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    ControlledValue,
    IdempotencyRecord,
    MethodologyVersion,
    ModerationCase,
    ModerationCaseState,
    ModerationSource,
    Organization,
    PublicationState,
    RecordVersion,
    TokenBudgetRecord,
    VerificationStatus,
)
from token_budget_platform.application.audit import AuditEntry, audit_on_commit
from token_budget_platform.application.catalog.public_projection_compat import (
    build_preview,
)
from token_budget_platform.application.jobs import TransactionalOutbox
from token_budget_platform.application.submission.capability_service import CapabilityService
from token_budget_platform.application.submission.duplicate_service import DuplicateDetectionService
from token_budget_platform.application.submission.service import SubmissionError, SubmissionService
from token_budget_platform.domain.ports import Clock

IDEMPOTENCY_TTL_DAYS = 7


class ConfirmationError(SubmissionError):
    """Raised when confirmation cannot proceed (validation or state failure)."""


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    """Outcome of a successful confirmation.

    ``management_token`` is the raw 256-bit capability returned exactly once;
    it is never persisted or logged (Req 8.1).
    """

    record_id: UUID
    public_reference: UUID
    organization_name: str
    ai_product: str
    token_budget: str
    reporting_start: date
    reporting_end: date
    verification_status: str
    management_token: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class IdempotentReplay:
    """A replayed confirmation referencing the originally created record."""

    record_id: UUID
    replayed: bool = True

    def as_result(self) -> ConfirmationResult:
        record = TokenBudgetRecord.objects.select_related("organization", "ai_product").get(
            id=self.record_id
        )
        return ConfirmationResult(
            record_id=record.id,
            public_reference=record.public_reference,
            organization_name=record.organization.name,
            ai_product=record.ai_product.label,
            token_budget=str(record.token_budget),
            reporting_start=record.reporting_start,
            reporting_end=record.reporting_end,
            verification_status=record.verification_status,
            management_token="",
            replayed=True,
        )


def preview_confirmation(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the exact public preview shown before confirmation (Req 5.8)."""
    return build_preview(payload)


class SubmissionConfirmationService:
    """Confirm a validated draft into an atomic, idempotent submission."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        capability_service: CapabilityService | None = None,
        duplicate_service: DuplicateDetectionService | None = None,
        outbox: TransactionalOutbox | None = None,
    ) -> None:
        self._clock = clock
        self._submissions = SubmissionService(clock=clock)
        self._capabilities = capability_service or CapabilityService(
            clock=clock or _SystemClockAdapter(),
            key_provider=_default_key_provider(),
            repository=_capability_repository(),
        )
        self._duplicates = duplicate_service or DuplicateDetectionService(clock=clock)
        self._outbox = outbox or TransactionalOutbox()

    # ── Confirmation ─────────────────────────────────────────────────────────

    def confirm(
        self,
        *,
        draft_id: UUID,
        draft_capability_token: str,
        requester_scope: str = "submission-web",
        idempotency_key: str = "",
        correlation_id: UUID | None = None,
    ) -> ConfirmationResult:
        """Confirm a draft into a queued Unverified record, atomically."""
        # Idempotent replay short-circuit (Req 7.1).
        if idempotency_key:
            replay = self._find_replay(requester_scope, idempotency_key)
            if replay is not None:
                return replay.as_result()

        draft = self._submissions._load_draft(draft_id, draft_capability_token)
        validation = self._submissions.validate_full(draft.payload)
        if not validation.valid:
            raise ConfirmationError("The draft is not complete; correct the listed errors first.")

        result = self._create_submission(
            payload=draft.payload,
            draft_id=draft.id,
            requester_scope=requester_scope,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        return result

    def confirm_payload(
        self,
        *,
        payload: dict[str, Any],
        requester_scope: str = "submission-api",
        idempotency_key: str = "",
        correlation_id: UUID | None = None,
    ) -> ConfirmationResult:
        """Confirm a fully validated payload directly (API path)."""
        if idempotency_key:
            replay = self._find_replay(requester_scope, idempotency_key)
            if replay is not None:
                return replay.as_result()
        return self._create_submission(
            payload=payload,
            draft_id=None,
            requester_scope=requester_scope,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    # ── Transaction ───────────────────────────────────────────────────────────

    def _create_submission(
        self,
        *,
        payload: dict[str, Any],
        draft_id: UUID | None,
        requester_scope: str,
        idempotency_key: str,
        correlation_id: UUID | None,
    ) -> ConfirmationResult:
        now = timezone.now()
        correlation = correlation_id or uuid4()

        resolved = _resolve_payload_references(payload)

        with transaction.atomic():
            organization = self._upsert_organization(resolved)

            methodology = (
                MethodologyVersion.objects.filter(is_active=True).order_by("-version").first()
            )
            if methodology is None:
                raise ConfirmationError("No active methodology version is configured.")

            record = TokenBudgetRecord.objects.create(
                organization=organization,
                budget_scope=resolved["budget_scope"],
                seat_count=resolved.get("seat_count"),
                workload_count=resolved.get("workload_count"),
                model_provider=resolved["model_provider"],
                ai_product=resolved["ai_product"],
                workload_category=resolved["workload_category"],
                region=resolved["region"],
                organization_search=organization.normalized_name,
                provider_search=resolved["model_provider"].normalized_label,
                product_search=resolved["ai_product"].normalized_label,
                token_budget=resolved["token_budget"],
                input_tokens=resolved.get("input_tokens"),
                output_tokens=resolved.get("output_tokens"),
                reporting_start=resolved["reporting_start"],
                reporting_end=resolved["reporting_end"],
                reporting_timezone=resolved.get("reporting_timezone", "UTC"),
                source_date=resolved["source_date"],
                token_usage=resolved.get("token_usage"),
                cost_minor_units=resolved.get("cost_minor_units"),
                cost_currency=resolved.get("cost_currency"),
                cost_basis=resolved.get("cost_basis"),
                methodology_notes=resolved.get("methodology_notes", ""),
                contributor_attribution=resolved.get("contributor_attribution", "ANONYMOUS"),
                publication_state=PublicationState.QUEUED,
                verification_status=VerificationStatus.UNVERIFIED,
                methodology_version=methodology,
            )

            # Immutable initial version (Req 5.7, 12.4).
            RecordVersion.objects.create(
                record=record,
                version=1,
                public_payload={"token_budget": str(record.token_budget)},
                private_payload={},
                changed_fields=[],
                change_reason="Initial submission",
                actor_type="CONTRIBUTOR",
            )

            # Duplicate detection: flag only, never auto-reject (Req 5.10).
            is_duplicate, fingerprint = self._duplicates.compute_and_check(
                organization_name=organization.name,
                budget_scope=record.budget_scope,
                ai_product_id=str(record.ai_product_id),
                token_budget=record.token_budget,
                reporting_start=record.reporting_start,
                reporting_end=record.reporting_end,
            )
            if is_duplicate:
                self._duplicates.flag_duplicate(record, fingerprint)
            else:
                self._duplicates.store_fingerprint(record, fingerprint)

            # Moderation queue case (Req 5.7, 7.2 entry).
            ModerationCase.objects.create(
                record=record,
                source=ModerationSource.SUBMISSION,
                category="NEW_SUBMISSION",
                state=ModerationCaseState.OPEN,
                priority=40,
                opened_at=now,
            )

            # Random private reference: capability digest only, raw token once.
            issued = self._capabilities.issue_capability(record.id)
            raw_token = issued.raw_token

            # Optional purpose-bound contact binding (Req 9.1, 9.3): the email
            # is encrypted separately and indexed only by keyed blind index.
            contact_email = payload.get("contact_email")
            if contact_email:
                from token_budget_platform.adapters.django.models import ContributorContact
                from token_budget_platform.application.submission.contact_cipher import (
                    email_blind_index,
                    encrypt_email,
                )

                ContributorContact.objects.create(
                    record=record,
                    encrypted_email=encrypt_email(str(contact_email)),
                    email_blind_index=email_blind_index(str(contact_email)),
                    wrapped_key_reference=uuid4(),
                    consent_purposes=["MANAGEMENT_RECOVERY"],
                    retention_until=now + timedelta(days=365),
                )

            # Outbox events: normalization + moderation notification (Req 5.7).
            self._outbox.enqueue(
                aggregate_type="TokenBudgetRecord",
                aggregate_id=record.id,
                event_type="RECALCULATE_METRICS",
                payload={"record_id": str(record.id)},
            )
            self._outbox.enqueue(
                aggregate_type="ModerationCase",
                aggregate_id=record.id,
                event_type="MODERATION_ENQUEUED",
                payload={"record_id": str(record.id)},
            )

            # Audit attempt exactly once, after commit (Req 7.10).
            audit_on_commit(
                AuditEntry(
                    actor_surrogate=None,
                    actor_role=None,
                    action="SUBMISSION_CONFIRMED",
                    target_type="TokenBudgetRecord",
                    target_id=record.id,
                    outcome="SUCCESS",
                    correlation_id=correlation,
                    sanitized_metadata={
                        "publication_state": PublicationState.QUEUED,
                        "budget_scope": record.budget_scope,
                        "duplicate_flag": record.duplicate_flag,
                    },
                )
            )

            # Idempotency marker (Req 7.1) — same transaction as the record.
            if idempotency_key:
                IdempotencyRecord.objects.create(
                    requester_scope=requester_scope[:128],
                    key_digest=_digest_key(requester_scope, idempotency_key),
                    request_hash=_digest_key(
                        "payload", json.dumps(_stable_payload(payload), sort_keys=True)
                    ),
                    response_status=201,
                    response_body_reference=str(record.id)[:255],
                    expires_at=now + timedelta(days=IDEMPOTENCY_TTL_DAYS),
                )

        return ConfirmationResult(
            record_id=record.id,
            public_reference=record.public_reference,
            organization_name=organization.name,
            ai_product=record.ai_product.label,
            token_budget=str(record.token_budget),
            reporting_start=record.reporting_start,
            reporting_end=record.reporting_end,
            verification_status=record.verification_status,
            management_token=raw_token,
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    def _find_replay(self, requester_scope: str, idempotency_key: str) -> IdempotentReplay | None:
        marker = IdempotencyRecord.objects.filter(
            requester_scope=requester_scope[:128],
            key_digest=_digest_key(requester_scope, idempotency_key),
        ).first()
        if marker is None:
            return None
        try:
            return IdempotentReplay(record_id=UUID(marker.response_body_reference))
        except ValueError:
            return None

    def _upsert_organization(self, resolved: dict[str, Any]) -> Organization:
        """Find the canonical organization by resolved name, or create it."""
        from token_budget_platform.application.governance.services import (
            ControlledVocabularyService,
            OrganizationNotFoundError,
        )

        vocabulary = ControlledVocabularyService()
        try:
            return vocabulary.resolve(resolved["organization_name"])
        except OrganizationNotFoundError:
            pass
        normalized = vocabulary.normalize(resolved["organization_name"])
        existing = Organization.objects.filter(normalized_name=normalized).first()
        if existing is not None:
            return existing
        return Organization.objects.create(
            name=resolved["organization_name"],
            normalized_name=normalized,
            slug=_slug_for(resolved["organization_name"]),
            industry=resolved.get("industry", "Unknown"),
            size_band=resolved["size_band"],
            region=resolved["region"],
        )


# ── Payload resolution helpers ────────────────────────────────────────────────


class _ResolvedPayload(dict):
    """Dict of resolved model references and typed values."""


def _resolve_payload_references(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve controlled-value IDs to rows and type strings/dates/decimals."""
    resolved: dict[str, Any] = dict(payload)

    # Form payloads carry ``*_id`` keys; normalize them to the base names.
    for base in ("size_band", "region", "model_provider", "ai_product", "workload_category"):
        id_key = f"{base}_id"
        if resolved.get(id_key) and not resolved.get(base):
            resolved[base] = resolved[id_key]

    for field in ("model_provider", "ai_product", "workload_category", "region", "size_band"):
        value = resolved.get(field)
        if value is None:
            continue
        if isinstance(value, str) and "-" in value:
            row = ControlledValue.objects.filter(id=value).first()
            if row is None:
                raise ConfirmationError("A selected classification value is no longer available.")
            resolved[field] = row

    resolved["budget_scope"] = payload.get("budget_scope", "TEAM")
    for field in ("reporting_start", "reporting_end", "source_date"):
        if isinstance(resolved.get(field), str):
            resolved[field] = date.fromisoformat(resolved[field])
    for field in (
        "token_budget",
        "input_tokens",
        "output_tokens",
        "token_usage",
        "cost_minor_units",
    ):
        if resolved.get(field) is not None and not isinstance(resolved[field], Decimal):
            resolved[field] = Decimal(str(resolved[field]))
    return resolved


def _slug_for(name: str) -> str:
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    base = slug or "organization"
    candidate = base
    suffix = 2
    while Organization.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _digest_key(scope: str, key: str) -> str:
    import hashlib

    return hashlib.sha256(f"{scope}:{key}".encode()).hexdigest()


def _stable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in sorted(payload)
        if isinstance(payload[key], (str, int, float, bool, type(None)))
    }


class _SystemClockAdapter:
    from token_budget_platform.domain.ports import Clock  # noqa: F401

    def now(self):  # pragma: no cover - trivial
        from django.utils import timezone as _tz

        return _tz.now()


def _default_key_provider():
    from django.conf import settings

    from token_budget_platform.adapters.hmac_keys import LocalHmacKeyProvider

    material = str(settings.SECRET_KEY).encode("utf-8")
    return LocalHmacKeyProvider(material[:64].ljust(32, b"0"))


def management_capability_service(clock: Clock | None = None) -> CapabilityService:
    """Capability service wired to deployment keys and Django persistence."""
    return CapabilityService(
        clock=clock or _SystemClockAdapter(),
        key_provider=_default_key_provider(),
        repository=_capability_repository(),
    )


def _capability_repository():
    """Django-backed capability repository for record management capabilities."""
    from datetime import datetime

    from token_budget_platform.adapters.django.models import ManagementCapability, ManagementSession
    from token_budget_platform.application.submission.capability_service import (
        CapabilityRecord,
        SessionRecord,
    )
    from token_budget_platform.domain.submission.capability import CapabilityScope

    class DjangoCapabilityRepository:
        def find_by_digest(self, token_digest: str) -> CapabilityRecord | None:
            row = ManagementCapability.objects.filter(token_digest=token_digest).first()
            return self._to_record(row) if row else None

        def find_active_for_record(self, record_id: UUID) -> list[CapabilityRecord]:
            rows = ManagementCapability.objects.filter(record_id=record_id, revoked_at__isnull=True)
            return [self._to_record(row) for row in rows if row.expires_at > timezone.now()]

        def save_capability(self, **kwargs) -> UUID:
            row = ManagementCapability.objects.create(
                record_id=kwargs["record_id"],
                key_version=kwargs["key_version"],
                token_digest=kwargs["token_digest"],
                scope=kwargs["scope"].value
                if hasattr(kwargs["scope"], "value")
                else str(kwargs["scope"]),
                issued_at=kwargs["issued_at"],
                expires_at=kwargs["expires_at"],
            )
            return row.id

        def mark_used(self, capability_id: UUID, used_at: datetime) -> None:
            ManagementCapability.objects.filter(id=capability_id).update(used_at=used_at)

        def revoke(self, capability_id: UUID, revoked_at: datetime) -> None:
            ManagementCapability.objects.filter(id=capability_id).update(revoked_at=revoked_at)

        def save_session(self, **kwargs) -> UUID:
            row = ManagementSession.objects.create(
                record_id=kwargs["record_id"],
                session_digest=kwargs["session_digest"],
                expires_at=kwargs["expires_at"],
            )
            return row.id

        def find_session_by_digest(self, session_digest: str) -> SessionRecord | None:
            row = ManagementSession.objects.filter(session_digest=session_digest).first()
            if row is None:
                return None
            return SessionRecord(
                id=row.id,
                record_id=row.record_id,
                session_digest=row.session_digest,
                expires_at=row.expires_at,
                revoked_at=row.revoked_at,
            )

        @staticmethod
        def _to_record(row: ManagementCapability) -> CapabilityRecord:
            return CapabilityRecord(
                id=row.id,
                record_id=row.record_id,
                key_version=row.key_version,
                token_digest=row.token_digest,
                scope=CapabilityScope(row.scope),
                issued_at=row.issued_at,
                expires_at=row.expires_at,
                used_at=row.used_at,
                revoked_at=row.revoked_at,
            )

    return DjangoCapabilityRepository()
