"""Deterministic backup-gate adapter used by local and test runtimes."""

from __future__ import annotations

from datetime import UTC, datetime

from token_budget_platform.domain.ports import GateDecision


class LocalBackupGate:
    def __init__(self, *, allowed: bool = True) -> None:
        self._decision = GateDecision(allowed=allowed, reason_code="INITIAL")

    def mutation_allowed(self) -> GateDecision:
        return self._decision

    def set_decision(
        self,
        allowed: bool,
        *,
        reason_code: str,
        source_reference: str = "local",
        observed_at: datetime | None = None,
    ) -> GateDecision:
        self._decision = GateDecision(
            allowed=allowed,
            reason_code=reason_code,
            source_reference=source_reference,
            observed_at=observed_at or datetime.now(UTC),
        )
        return self._decision
