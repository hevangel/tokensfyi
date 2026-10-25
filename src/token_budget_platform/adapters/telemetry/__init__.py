"""Structured, in-memory telemetry adapter for local verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)


class LocalTelemetry:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []
        self.metrics: list[TelemetryEvent] = []

    def emit(self, event_name: str, *, attributes: dict[str, Any] | None = None) -> None:
        self.events.append(TelemetryEvent(event_name, dict(attributes or {})))

    def increment(
        self, metric_name: str, *, value: int = 1, attributes: dict[str, str] | None = None
    ) -> None:
        values: dict[str, Any] = dict(attributes or {})
        values["value"] = value
        self.metrics.append(TelemetryEvent(metric_name, values))


class LocalCacheInvalidation:
    def __init__(self) -> None:
        self.invalidated: list[str] = []

    def invalidate(self, cache_key: str) -> None:
        if cache_key:
            self.invalidated.append(cache_key)
