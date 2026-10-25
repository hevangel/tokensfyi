"""Cross-cutting telemetry domain logic.

Provides:
- Correlation ID generation and propagation
- Allowlist-based structured log sanitization (Req 9.8)
- Sanitized audit-failure error construction (Req 7.12)
- Safe error classification for public responses (Req 16.11)

When operational logs are created, credentials, Submission_Management_Link values,
Personal_Data, Evidence content, bodies, raw private text, and exception details
are redacted from all operational output.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Correlation ID context propagation
# ---------------------------------------------------------------------------

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Return the current correlation ID, or empty string if unset."""
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    """Set the correlation ID for the current context."""
    _correlation_id.set(value)


def generate_correlation_id() -> str:
    """Generate a new UUIDv4 correlation ID."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Allowlist-based sanitization
# ---------------------------------------------------------------------------

# Fields that are safe to include in operational output. Anything not on this
# list is redacted.
ALLOWED_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "timestamp",
        "level",
        "logger",
        "message",
        "correlation_id",
        "deployment_version",
        "environment",
        "operation",
        "action",
        "target_type",
        "target_id",
        "actor_surrogate",
        "outcome",
        "status_code",
        "method",
        "path",
        "duration_ms",
        "error_category",
        "job_type",
        "job_id",
        "scan_outcome",
        "notification_channel",
        "metric_name",
        "metric_value",
        "gate_state",
        "source_pseudonym",
        "service_name",
        "hostname",
        "pid",
        "thread",
    }
)

# Patterns that identify sensitive content to redact even in allowed fields
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    # Capability tokens / management links
    re.compile(r"(?i)(capability|management[_-]?link|token)[=:]\s*\S+"),
    # Email addresses
    re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    # AWS keys
    re.compile(r"(?i)(AKIA|aws[_-]?secret|aws[_-]?access)[A-Za-z0-9/+=]+"),
    # Generic secrets
    re.compile(r"(?i)(password|secret|credential|api[_-]?key)[=:]\s*\S+"),
    # UUIDs that look like capability tokens (256-bit hex)
    re.compile(r"[0-9a-fA-F]{64}"),
]

REDACTED = "[REDACTED]"


def sanitize_value(value: Any) -> Any:
    """Redact sensitive patterns from a string value."""
    if not isinstance(value, str):
        return value
    result = value
    for pattern in _SENSITIVE_PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result


def sanitize_log_record(record: dict[str, Any]) -> dict[str, Any]:
    """Apply allowlist filtering and value sanitization to a log record.

    Only fields in ALLOWED_LOG_FIELDS pass through. Values in allowed fields
    are further scrubbed for sensitive patterns. Fields not in the allowlist
    are dropped entirely.
    """
    sanitized: dict[str, Any] = {}
    for key, value in record.items():
        if key in ALLOWED_LOG_FIELDS:
            sanitized[key] = sanitize_value(value)
    return sanitized


# ---------------------------------------------------------------------------
# Sanitized audit failure events (Req 7.12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SanitizedAuditFailure:
    """A sanitized operational error emitted when Audit_Record creation fails.

    Contains only actor surrogate, action, target surrogate, and correlation ID.
    No submitted content, evidence, email, capability, or free text.
    """

    actor_surrogate: str
    action: str
    target_surrogate: str
    correlation_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_category: str = "audit_write_failure"

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": "ERROR",
            "message": "Audit record creation failed",
            "correlation_id": self.correlation_id,
            "actor_surrogate": self.actor_surrogate,
            "action": self.action,
            "target_type": "audit_record",
            "target_id": self.target_surrogate,
            "error_category": self.error_category,
            "operation": "audit_sink",
        }


# ---------------------------------------------------------------------------
# Safe error classification (Req 16.5, 16.11)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OperationalError:
    """Structured operational error for internal logging (Req 16.5)."""

    timestamp: datetime
    correlation_id: str
    deployment_version: str
    operation: str
    error_category: str

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": "ERROR",
            "message": f"Unhandled error in {self.operation}",
            "correlation_id": self.correlation_id,
            "deployment_version": self.deployment_version,
            "operation": self.operation,
            "error_category": self.error_category,
        }


class ErrorClassification:
    """Classifies exceptions into safe public categories without leaking details."""

    # Map exception types to safe public categories
    CATEGORY_MAP: dict[str, str] = {
        "ValidationError": "validation_error",
        "PermissionDenied": "authorization_error",
        "NotAuthenticated": "authentication_error",
        "Http404": "not_found",
        "ObjectDoesNotExist": "not_found",
        "IntegrityError": "conflict",
        "OperationalError": "service_unavailable",
        "Timeout": "service_unavailable",
        "TimeoutError": "service_unavailable",
        "ConnectionError": "service_unavailable",
    }

    @classmethod
    def classify(cls, exc: BaseException) -> str:
        """Return a safe error category string, never the exception message."""
        exc_name = type(exc).__name__
        return cls.CATEGORY_MAP.get(exc_name, "internal_error")

    @classmethod
    def is_retryable(cls, category: str) -> bool:
        """Whether the classified error suggests the client may retry."""
        return category in ("service_unavailable",)
