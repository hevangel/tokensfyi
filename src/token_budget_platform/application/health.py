"""Application-level health contracts shared by both transports.

Design specification:
- `/health/live` proves only that the process event loop responds.
- `/health/ready` returns deployment version and coarse ok/degraded/unavailable states
  for database, object storage, scanner/job processing, and backup mutation gate.
- It exposes no endpoint names, credentials, content, counts tied to people, or
  exception text.
- Public reads may remain ready during noncritical mail degradation.
- Data-changing readiness becomes unavailable when the backup gate is closed.

Requirements: 15.5, 16.3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DependencyStatus(str, Enum):
    """Coarse dependency health status exposed by the readiness endpoint."""

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ReadinessStatus(str, Enum):
    """Overall readiness level for the application."""

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DependencyHealth:
    """Health state of a single dependency without private details."""

    name: str
    status: DependencyStatus


@dataclass(frozen=True, slots=True)
class LivenessResult:
    """Liveness response: process event loop is responding."""

    status: str = "ok"


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Readiness response with deployment version and dependency states."""

    status: ReadinessStatus
    version: str
    dependencies: dict[str, DependencyStatus] = field(default_factory=dict)
    mutation_allowed: bool = True


class HealthService:
    """Coordinates dependency health checks for liveness/readiness endpoints.

    Each dependency probe is a callable returning DependencyHealth.
    The service aggregates results into a ReadinessResult without exposing
    private details (no endpoint names, credentials, content, counts, or
    exception text).
    """

    def __init__(self, *, version: str) -> None:
        self._version = version
        self._probes: dict[str, Any] = {}

    def register_probe(self, name: str, probe: Any) -> None:
        """Register a health probe callable that returns DependencyHealth."""
        self._probes[name] = probe

    def liveness(self) -> LivenessResult:
        """Check liveness: process event loop responds."""
        return LivenessResult(status="ok")

    def readiness(self) -> ReadinessResult:
        """Check readiness: deployment version + coarse dependency states.

        Rules:
        - If database is unavailable, overall status is unavailable.
        - If backup gate is closed (mutations disallowed), overall status is
          degraded for reads but data-changing readiness is unavailable.
        - If any other dependency is unavailable, overall status is degraded.
        - If all dependencies are ok, overall status is ok.
        """
        dependency_states: dict[str, DependencyStatus] = {}
        mutation_allowed = True

        for name, probe in self._probes.items():
            try:
                health: DependencyHealth = probe()
                dependency_states[name] = health.status
            except Exception:
                # Never expose exception text in health response
                dependency_states[name] = DependencyStatus.UNAVAILABLE

        # Determine overall status
        overall = ReadinessStatus.OK

        # Database unavailable means entirely unavailable
        if dependency_states.get("database") == DependencyStatus.UNAVAILABLE:
            overall = ReadinessStatus.UNAVAILABLE
        else:
            # Check backup gate - closed gate means data-changing is unavailable
            if dependency_states.get("backup_gate") == DependencyStatus.UNAVAILABLE:
                mutation_allowed = False
                # Read availability is degraded when backup gate is closed
                overall = ReadinessStatus.DEGRADED

            # Any other unavailable dependency degrades the service
            for name, status in dependency_states.items():
                if name in ("database", "backup_gate"):
                    continue
                if status == DependencyStatus.UNAVAILABLE:
                    if overall == ReadinessStatus.OK:
                        overall = ReadinessStatus.DEGRADED
                elif status == DependencyStatus.DEGRADED:
                    if overall == ReadinessStatus.OK:
                        overall = ReadinessStatus.DEGRADED

        return ReadinessResult(
            status=overall,
            version=self._version,
            dependencies=dependency_states,
            mutation_allowed=mutation_allowed,
        )
