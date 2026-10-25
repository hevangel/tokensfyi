"""Signed backup-state ingestion and production mutation gate.

Implements the backup event ingestion protocol and mutation gate that suspends
production data-changing operations after a required backup failure and reopens
the gate only when a subsequent required backup succeeds.

Requirements:
- 16.7: Required backups support a 24-hour recovery interval.
- 16.8: Backup failure suspends data-changing operations until a backup succeeds.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from token_budget_platform.domain.ports import GateDecision

logger = logging.getLogger("token_budget_platform.backup_gate")


class InvalidBackupEvent(ValueError):
    """Raised when a backup event has an invalid signature or structure."""


class SignedBackupGate:
    """Accept only authenticated required-backup events before changing the gate.

    The gate starts closed (mutations blocked) in production to enforce
    fail-safe behavior: until a successful backup event is received,
    the system refuses mutations.
    """

    def __init__(self, signing_key: bytes, *, initially_allowed: bool = False) -> None:
        if len(signing_key) < 32:
            raise ValueError("backup signing key must contain at least 32 bytes")
        self._signing_key = signing_key
        self._decision = GateDecision(allowed=initially_allowed, reason_code="INITIAL")

    def mutation_allowed(self) -> GateDecision:
        return self._decision

    def ingest(self, event: Mapping[str, Any], signature: str) -> GateDecision:
        """Verify signature and ingest a backup state event.

        The event must contain:
        - status: "SUCCEEDED" or "FAILED"
        - reference: A source reference string (optional, defaults to "backup-event")
        - observed_at: ISO 8601 timestamp (optional, defaults to now)

        Raises InvalidBackupEvent if the signature is wrong or the event is malformed.
        """
        canonical = json.dumps(
            dict(sorted(event.items())), sort_keys=True, separators=(",", ":")
        ).encode()
        expected = hmac.new(self._signing_key, canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            logger.warning(
                "backup_event_signature_invalid",
                extra={"reference": str(event.get("reference", "unknown"))},
            )
            raise InvalidBackupEvent("backup event signature is invalid")

        status = str(event.get("status", "")).upper()
        if status not in {"SUCCEEDED", "FAILED"}:
            raise InvalidBackupEvent(
                f"backup event status must be SUCCEEDED or FAILED, got: {event.get('status')!r}"
            )

        observed = _parse_time(event.get("observed_at"))
        allowed = status == "SUCCEEDED"
        reason = "BACKUP_SUCCEEDED" if allowed else "BACKUP_FAILED"

        self._decision = GateDecision(
            allowed=allowed,
            reason_code=reason,
            observed_at=observed,
            source_reference=str(event.get("reference", "backup-event")),
        )

        logger.info(
            "backup_gate_state_changed",
            extra={
                "allowed": allowed,
                "reason_code": reason,
                "source_reference": self._decision.source_reference,
            },
        )
        return self._decision


def _parse_time(value: Any) -> datetime:
    """Parse an ISO 8601 timestamp from a backup event payload."""
    if not value:
        return datetime.now(UTC)
    if not isinstance(value, str):
        raise InvalidBackupEvent("backup event observed_at must be an ISO 8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidBackupEvent(
            f"backup event observed_at is not valid ISO 8601: {value!r}"
        ) from exc
