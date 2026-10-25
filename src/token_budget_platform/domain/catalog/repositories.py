"""Repository contracts for catalog reads.

Public callers receive only repositories that enforce the eligibility predicate;
ORM managers are intentionally not part of this boundary.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class PublicRecordRepository(Protocol):
    def eligible(self) -> Any: ...

    def for_organization(self, organization_id: UUID) -> Any: ...

    def search(self, *, organization: str = "", provider: str = "", product: str = "") -> Any: ...


class RecordVersionRepository(Protocol):
    def for_record(self, record_id: UUID) -> Any: ...


class ModerationCaseRepository(Protocol):
    def open_for_record(self, record_id: UUID) -> Any: ...
