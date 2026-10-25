"""Dependency-inversion contracts for infrastructure integrations.

The domain uses these small value objects and protocols instead of importing SDKs
or local service clients.  Production and deterministic local adapters implement
exactly the same contracts.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, BinaryIO, Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    content_type: str = "application/octet-stream"
    byte_count: int = 0
    checksum: str = ""
    encryption_key_id: UUID | None = None
    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    outcome: str
    scanner_version: str = "local-1"
    signature: str = ""


@dataclass(frozen=True, slots=True)
class Notification:
    channel: str
    destination: str
    template: str
    reference_id: UUID | None = None
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeliveryReference:
    provider: str
    message_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class WrappedDataKey:
    key_id: UUID
    ciphertext: bytes
    algorithm: str = "local-hmac-xor-v1"
    provider_reference: str = ""


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    reason_code: str = ""
    observed_at: datetime | None = None
    source_reference: str = ""


class Clock(Protocol):
    def now(self) -> datetime: ...


class ObjectStorePort(Protocol):
    def put_encrypted(self, key: str, stream: BinaryIO, metadata: ObjectMetadata) -> None: ...

    def open_encrypted(self, key: str) -> AbstractContextManager[BinaryIO]: ...

    def delete(self, key: str) -> None: ...

    def metadata(self, key: str) -> ObjectMetadata: ...


class MalwareScannerPort(Protocol):
    def scan(self, stream: BinaryIO) -> ScanResult: ...


class NotificationPort(Protocol):
    def send(self, message: Notification) -> DeliveryReference: ...


class KeyManagementPort(Protocol):
    def generate_data_key(self, purpose: str) -> WrappedDataKey: ...

    def decrypt_data_key(self, wrapped: WrappedDataKey) -> bytes: ...

    def destroy_reference(self, key_id: UUID) -> None: ...


class TelemetryPort(Protocol):
    def emit(self, event_name: str, *, attributes: dict[str, Any] | None = None) -> None: ...

    def increment(
        self, metric_name: str, *, value: int = 1, attributes: dict[str, str] | None = None
    ) -> None: ...


class CacheInvalidationPort(Protocol):
    def invalidate(self, cache_key: str) -> None: ...


class BackupGatePort(Protocol):
    def mutation_allowed(self) -> GateDecision: ...


# Descriptive aliases keep adapter integrations readable without breaking the
# shorter names used in the original design contract.
ClockPort = Clock
ScanningPort = MalwareScannerPort
