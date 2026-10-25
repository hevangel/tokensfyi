"""Application services for controlled vocabularies and organization identity.

The services deliberately keep historical rows addressable: publishing a changed
controlled value creates a new version, and organization merges redirect the
organization relationship without rewriting record history or audit targets.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    ControlledValue,
    ControlledValueKind,
    Organization,
    OrganizationAlias,
    TokenBudgetRecord,
)

CONTROLLED_VALUE_KINDS: tuple[str, ...] = tuple(kind for kind, _ in ControlledValueKind.choices)
_WHITESPACE = re.compile(r"\s+")


class GovernanceError(ValueError):
    """Base error for invalid governance operations."""


class AliasConflictError(GovernanceError):
    """Raised when an active alias would identify more than one organization."""


class OrganizationNotFoundError(GovernanceError):
    """Raised when a submitted organization cannot be resolved."""


class OrganizationMergeError(GovernanceError):
    """Raised when an organization merge would be invalid or cyclic."""


@dataclass(frozen=True, slots=True)
class OrganizationMergeResult:
    """Stable result of one canonical organization merge."""

    source_id: UUID
    canonical_id: UUID
    records_reassigned: int
    aliases_redirected: int


class ControlledVocabularyService:
    """Manage temporal controlled values and organization aliases."""

    @staticmethod
    def normalize(value: str) -> str:
        """Normalize human-entered labels for matching, without changing display text."""
        if not isinstance(value, str):
            raise GovernanceError("value must be text")
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return _WHITESPACE.sub(" ", normalized).strip()

    @staticmethod
    def _validate_kind(kind: str) -> str:
        if kind not in CONTROLLED_VALUE_KINDS:
            raise GovernanceError(f"unsupported controlled-value kind: {kind}")
        return kind

    def active_values(self, kind: str, *, at: datetime | None = None):
        """Return values active at ``at``; omitted time means the current instant."""
        self._validate_kind(kind)
        moment = at or timezone.now()
        return (
            ControlledValue.objects.filter(
                kind=kind,
                active_from__lte=moment,
            )
            .filter(Q(active_to__isnull=True) | Q(active_to__gt=moment))
            .order_by("label", "code", "id")
        )

    def active_for_new_draft(
        self, *, at: datetime | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Return the active, serializable vocabulary snapshot available to a new draft."""
        moment = at or timezone.now()
        values = (
            ControlledValue.objects.filter(kind__in=CONTROLLED_VALUE_KINDS, active_from__lte=moment)
            .filter(Q(active_to__isnull=True) | Q(active_to__gt=moment))
            .order_by("kind", "label", "code", "id")
        )
        snapshot: dict[str, list[dict[str, Any]]] = {kind: [] for kind in CONTROLLED_VALUE_KINDS}
        for value in values:
            snapshot[value.kind].append(
                {
                    "id": str(value.id),
                    "code": value.code,
                    "label": value.label,
                    "version": value.vocabulary_version,
                }
            )
        return snapshot

    def versions_for_new_draft(self, *, at: datetime | None = None) -> dict[str, int]:
        """Return the maximum active vocabulary version captured by a new draft."""
        return {
            kind: max((int(entry["version"]) for entry in values), default=0)
            for kind, values in self.active_for_new_draft(at=at).items()
        }

    @transaction.atomic
    def publish_value(
        self,
        *,
        kind: str,
        code: str,
        label: str,
        effective_at: datetime | None = None,
        vocabulary_version: int | None = None,
        parent: ControlledValue | None = None,
    ) -> ControlledValue:
        """Publish a new version of a value while retaining the old row for records."""
        self._validate_kind(kind)
        code = self.normalize(code)
        label = label.strip() if isinstance(label, str) else ""
        if not code or not label:
            raise GovernanceError("controlled values require non-empty code and label")
        moment = effective_at or timezone.now()
        current = (
            ControlledValue.objects.select_for_update()
            .filter(kind=kind, code=code)
            .filter(Q(active_to__isnull=True) | Q(active_to__gt=moment), active_from__lte=moment)
            .order_by("-vocabulary_version", "-id")
            .first()
        )
        if (
            current is not None
            and current.label == label
            and current.parent_id == (parent.id if parent else None)
        ):
            return current
        latest = (
            ControlledValue.objects.filter(kind=kind)
            .order_by("-vocabulary_version")
            .values_list("vocabulary_version", flat=True)
            .first()
        )
        version = vocabulary_version if vocabulary_version is not None else (latest or 0) + 1
        if version < 1:
            raise GovernanceError("vocabulary_version must be positive")
        if ControlledValue.objects.filter(
            kind=kind, code=code, vocabulary_version=version
        ).exists():
            raise GovernanceError("controlled-value version already exists")
        if current is not None:
            current.active_to = moment
            current.save(update_fields=["active_to", "row_version", "updated_at"])
        return ControlledValue.objects.create(
            kind=kind,
            code=code,
            label=label,
            normalized_label=self.normalize(label),
            parent=parent,
            active_from=moment,
            vocabulary_version=version,
        )

    @transaction.atomic
    def deactivate_value(
        self, value_id: UUID, *, effective_at: datetime | None = None
    ) -> ControlledValue:
        """End a value's active interval without invalidating existing references."""
        value = ControlledValue.objects.select_for_update().get(id=value_id)
        moment = effective_at or timezone.now()
        if moment < value.active_from:
            raise GovernanceError("deactivation cannot precede activation")
        if value.active_to is None or value.active_to > moment:
            value.active_to = moment
            value.save(update_fields=["active_to", "row_version", "updated_at"])
        return value

    @transaction.atomic
    def add_alias(
        self,
        *,
        organization: Organization | UUID,
        alias: str,
        provenance: str,
        active_from: datetime | None = None,
        active_to: datetime | None = None,
    ) -> OrganizationAlias:
        """Add an active normalized alias, rejecting overlapping cross-organization matches."""
        organization_id = (
            organization.id if isinstance(organization, Organization) else organization
        )
        canonical = OrganizationMergeService._canonical_locked(organization_id, lock=False)
        normalized = self.normalize(alias)
        if not normalized or not provenance.strip():
            raise GovernanceError("aliases require text and provenance")
        moment = active_from or timezone.now()
        if active_to is not None and active_to <= moment:
            raise GovernanceError("alias active_to must follow active_from")
        overlapping = (
            OrganizationAlias.objects.filter(normalized_alias=normalized)
            .filter(
                Q(active_to__isnull=True) | Q(active_to__gt=moment),
                active_from__lt=(active_to or datetime.max),
            )
            .exclude(organization_id=canonical.id)
        )
        for existing in overlapping.select_related("organization"):
            if (
                OrganizationMergeService._canonical_locked(existing.organization_id, lock=False).id
                != canonical.id
            ):
                raise AliasConflictError("alias is already active for another organization")
        current_active = OrganizationAlias.objects.filter(
            organization_id=canonical.id, normalized_alias=normalized, active_to__isnull=True
        ).first()
        if current_active is not None:
            return current_active
        return OrganizationAlias.objects.create(
            organization_id=canonical.id,
            alias=alias.strip(),
            normalized_alias=normalized,
            provenance=provenance.strip(),
            active_from=moment,
            active_to=active_to,
        )

    def resolve(self, submitted_name: str, *, at: datetime | None = None) -> Organization:
        """Resolve a canonical organization by normalized name or active alias."""
        normalized = self.normalize(submitted_name)
        if not normalized:
            raise OrganizationNotFoundError("organization name is empty")
        moment = at or timezone.now()
        organizations = Organization.objects.filter(normalized_name=normalized, is_active=True)
        # Merged organizations keep their historical name resolvable: follow the
        # merge chain so references recorded before the merge still resolve.
        merged = Organization.objects.filter(normalized_name=normalized, is_active=False).exclude(
            merge_target__isnull=True
        )
        aliases = OrganizationAlias.objects.filter(
            normalized_alias=normalized,
            active_from__lte=moment,
        ).filter(Q(active_to__isnull=True) | Q(active_to__gt=moment))
        candidates: dict[UUID, Organization] = {}
        for organization in organizations:
            canonical = OrganizationMergeService._canonical_locked(organization.id, lock=False)
            candidates[canonical.id] = canonical
        for organization in merged:
            canonical = OrganizationMergeService._canonical_locked(organization.id, lock=False)
            candidates[canonical.id] = canonical
        for alias in aliases.select_related("organization"):
            canonical = OrganizationMergeService._canonical_locked(
                alias.organization_id, lock=False
            )
            candidates[canonical.id] = canonical
        if not candidates:
            raise OrganizationNotFoundError(
                "organization name does not match a canonical organization or alias"
            )
        if len(candidates) > 1:
            raise AliasConflictError(
                "organization name matches more than one canonical organization"
            )
        return next(iter(candidates.values()))


class OrganizationMergeService:
    """Merge duplicate profiles without rewriting identity-linked record history."""

    @staticmethod
    def _canonical_locked(organization_id: UUID, *, lock: bool) -> Organization:
        queryset: QuerySet[Organization] = Organization.objects.all()
        if lock:
            queryset = queryset.select_for_update()
        current = queryset.get(id=organization_id)
        visited: set[UUID] = set()
        while current.merge_target_id is not None:
            if current.id in visited:
                raise OrganizationMergeError("organization merge target cycle detected")
            visited.add(current.id)
            current = queryset.get(id=current.merge_target_id)
        return current

    def canonical(self, organization_id: UUID) -> Organization:
        return self._canonical_locked(organization_id, lock=False)

    @transaction.atomic
    def merge(
        self,
        *,
        source: Organization | UUID,
        canonical: Organization | UUID,
        effective_at: datetime | None = None,
    ) -> OrganizationMergeResult:
        """Redirect ``source`` to ``canonical`` atomically and preserve all child identities."""
        source_id = source.id if isinstance(source, Organization) else source
        canonical_id = canonical.id if isinstance(canonical, Organization) else canonical
        moment = effective_at or timezone.now()
        source_row = Organization.objects.select_for_update().get(id=source_id)
        target_row = Organization.objects.select_for_update().get(id=canonical_id)
        source_root = self._canonical_locked(source_row.id, lock=True)
        target_root = self._canonical_locked(target_row.id, lock=True)
        if source_root.id == target_root.id:
            raise OrganizationMergeError("source and canonical organization are already the same")
        if not target_root.is_active:
            raise OrganizationMergeError("canonical organization must be active")
        if source_root.id != source_row.id:
            raise OrganizationMergeError("source organization is already redirected")

        source_aliases = list(
            OrganizationAlias.objects.select_for_update()
            .filter(organization_id=source_root.id)
            .order_by("id")
        )
        target_aliases = {
            alias.normalized_alias: alias
            for alias in OrganizationAlias.objects.select_for_update().filter(
                organization_id=target_root.id
            )
        }
        redirected = 0
        for alias in source_aliases:
            existing = target_aliases.get(alias.normalized_alias)
            if existing is not None and existing.id != alias.id:
                if alias.active_to is None or alias.active_to > moment:
                    alias.active_to = moment
                    alias.save(update_fields=["active_to", "row_version", "updated_at"])
                continue
            alias.organization_id = target_root.id
            alias.save(update_fields=["organization", "row_version", "updated_at"])
            target_aliases[alias.normalized_alias] = alias
            redirected += 1

        records_reassigned = TokenBudgetRecord.objects.filter(
            organization_id=source_root.id
        ).update(organization_id=target_root.id)
        source_root.merge_target_id = target_root.id
        source_root.is_active = False
        source_root.save(update_fields=["merge_target", "is_active", "row_version", "updated_at"])
        return OrganizationMergeResult(
            source_root.id, target_root.id, records_reassigned, redirected
        )
