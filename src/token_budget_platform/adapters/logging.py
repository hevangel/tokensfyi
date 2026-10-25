"""Structured JSON logging with deployment metadata and sanitization.

Provides:
- A JSON formatter that emits allowlisted structured log records
- Integration with the correlation ID context variable
- Deployment and operation metadata injection
- Allowlist filtering that redacts credentials, Submission_Management_Link
  values, Personal_Data, Evidence content, bodies, raw private text, and
  exception details from all operational output (Req 9.8)

Requirements: 9.8, 16.5
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from token_budget_platform.domain.telemetry import (
    ALLOWED_LOG_FIELDS,
    get_correlation_id,
    sanitize_log_record,
    sanitize_value,
)


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON with deployment metadata.

    Applies the allowlist-based sanitization filter to every record before
    output.  Fields not in the allowlist are dropped.  Values in allowed
    fields are further scrubbed for sensitive patterns.

    Exception tracebacks are replaced with only the sanitized error_category.
    """

    def __init__(
        self,
        *,
        deployment_version: str | None = None,
        environment: str | None = None,
        service_name: str = "token-budget-platform",
    ) -> None:
        super().__init__()
        self._deployment_version = deployment_version or os.environ.get(
            "DEPLOYMENT_VERSION", "local-dev"
        )
        self._environment = environment or os.environ.get("ENVIRONMENT", "local")
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        # Build the base structured record
        raw: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
            "deployment_version": self._deployment_version,
            "environment": self._environment,
            "service_name": self._service_name,
            "hostname": os.environ.get("HOSTNAME", ""),
            "pid": record.process or 0,
            "thread": record.threadName or "",
        }

        # Copy any extra fields that match the allowlist
        for key in ALLOWED_LOG_FIELDS:
            if key not in raw and hasattr(record, key):
                raw[key] = getattr(record, key)

        # Handle exception info safely — never include traceback in output
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            raw["error_category"] = type(exc).__name__
            # Do NOT include exc_text, stack trace, or exception message

        # Apply allowlist sanitization
        sanitized = sanitize_log_record(raw)
        return json.dumps(sanitized, default=str, ensure_ascii=False)


class SanitizingFilter(logging.Filter):
    """Logging filter that redacts sensitive content from log record messages.

    Applies to messages and args before they reach the formatter.  This is a
    defense-in-depth measure — the formatter also sanitizes, but the filter
    catches content before it flows to any handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanitize the message string
        if record.msg and isinstance(record.msg, str):
            record.msg = sanitize_value(record.msg)

        # Sanitize format args
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: sanitize_value(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(sanitize_value(a) for a in record.args)

        return True


def get_structured_logging_config(
    *,
    deployment_version: str = "local-dev",
    environment: str = "local",
    log_level: str = "INFO",
) -> dict[str, Any]:
    """Return a Django LOGGING dict config for structured JSON output.

    Suitable for assignment to settings.LOGGING.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "sanitizing": {
                "()": "token_budget_platform.adapters.logging.SanitizingFilter",
            },
        },
        "formatters": {
            "structured_json": {
                "()": "token_budget_platform.adapters.logging.StructuredJsonFormatter",
                "deployment_version": deployment_version,
                "environment": environment,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "structured_json",
                "filters": ["sanitizing"],
            },
        },
        "root": {
            "level": log_level,
            "handlers": ["console"],
        },
        "loggers": {
            "token_budget_platform": {
                "level": log_level,
                "handlers": ["console"],
                "propagate": False,
            },
            "django": {
                "level": "WARNING",
                "handlers": ["console"],
                "propagate": False,
            },
            "django.request": {
                "level": "WARNING",
                "handlers": ["console"],
                "propagate": False,
            },
        },
    }
