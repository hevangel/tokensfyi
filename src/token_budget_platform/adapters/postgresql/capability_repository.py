"""Django ORM implementation of CapabilityRepository.

Persists capability digests and management sessions using the Django models.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone as django_tz

from token_budget_platform.adapters.django.models import (
    ManagementCapability,
    ManagementSession,
    uuid7,
)
from token_budget_platform.application.submission.capability_service import (
    CapabilityRecord,
    SessionRecord,
)
from token_budget_platform.domain.submission.capability import CapabilityScope


class DjangoCapabilityRepository:
    """ORM-backed repository for capabilities and sessions."""

    def find_by_digest(self, token_digest: str) -> CapabilityRecord | None:
        """Look up a capability by its HMAC digest."""
        try:
            obj = ManagementCapability.objects.get(token_digest=token_digest)
        except ManagementCapability.DoesNotExist:
            return None
        return self._to_record(obj)

    def find_active_for_record(self, record_id: UUID) -> list[CapabilityRecord]:
        """Return all non-revoked, non-expired capabilities for a record."""
        now = django_tz.now()
        qs = ManagementCapability.objects.filter(
            record_id=record_id,
            revoked_at__isnull=True,
            expires_at__gt=now,
        )
        return [self._to_record(obj) for obj in qs]

    @transaction.atomic
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
        """Persist a new capability record."""
        obj = ManagementCapability(
            id=uuid7(),
            record_id=record_id,
            key_version=key_version,
            token_digest=token_digest,
            scope=str(scope),
            issued_at=issued_at,
            expires_at=expires_at,
        )
        obj.save()
        return obj.id

    def mark_used(self, capability_id: UUID, used_at: datetime) -> None:
        """Record the time a capability was last used."""
        ManagementCapability.objects.filter(id=capability_id).update(used_at=used_at)

    def revoke(self, capability_id: UUID, revoked_at: datetime) -> None:
        """Mark a capability as revoked."""
        ManagementCapability.objects.filter(id=capability_id).update(revoked_at=revoked_at)

    @transaction.atomic
    def save_session(
        self,
        *,
        record_id: UUID,
        session_digest: str,
        expires_at: datetime,
    ) -> UUID:
        """Persist a management session."""
        obj = ManagementSession(
            id=uuid7(),
            record_id=record_id,
            session_digest=session_digest,
            expires_at=expires_at,
        )
        obj.save()
        return obj.id

    def find_session_by_digest(self, session_digest: str) -> SessionRecord | None:
        """Look up a session by its digest."""
        try:
            obj = ManagementSession.objects.get(session_digest=session_digest)
        except ManagementSession.DoesNotExist:
            return None
        return SessionRecord(
            id=obj.id,
            record_id=obj.record_id,
            session_digest=obj.session_digest,
            expires_at=obj.expires_at,
            revoked_at=obj.revoked_at,
        )

    @staticmethod
    def _to_record(obj: ManagementCapability) -> CapabilityRecord:
        return CapabilityRecord(
            id=obj.id,
            record_id=obj.record_id,
            key_version=obj.key_version,
            token_digest=obj.token_digest,
            scope=CapabilityScope(obj.scope),
            issued_at=obj.issued_at,
            expires_at=obj.expires_at,
            used_at=obj.used_at,
            revoked_at=obj.revoked_at,
        )
