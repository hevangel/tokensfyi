"""Tests for correlation ID propagation, structured log sanitization, and safe error translation.

Covers:
- Correlation ID middleware generates/propagates IDs (Req 16.5)
- Allowlist log sanitization redacts sensitive fields (Req 9.8)
- Safe error translation returns problem+json without details (Req 16.11)
- SanitizedAuditFailure contains only allowed fields (Req 7.12)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings

from token_budget_platform.adapters.logging import (
    SanitizingFilter,
    StructuredJsonFormatter,
)
from token_budget_platform.domain.telemetry import (
    ALLOWED_LOG_FIELDS,
    REDACTED,
    ErrorClassification,
    OperationalError,
    SanitizedAuditFailure,
    generate_correlation_id,
    get_correlation_id,
    sanitize_log_record,
    sanitize_value,
    set_correlation_id,
)
from token_budget_platform.transports.middleware.correlation import (
    CORRELATION_HEADER,
    CorrelationIdMiddleware,
)
from token_budget_platform.transports.middleware.error_translation import (
    SafeErrorTranslationMiddleware,
)

# ---------------------------------------------------------------------------
# Correlation ID tests
# ---------------------------------------------------------------------------


class TestCorrelationIdMiddleware:
    """Tests for CorrelationIdMiddleware."""

    def setup_method(self) -> None:
        self.factory = RequestFactory()

    def _make_middleware(
        self, inner_response: HttpResponse | None = None
    ) -> CorrelationIdMiddleware:
        response = inner_response or HttpResponse("OK")

        def get_response(request: HttpRequest) -> HttpResponse:
            # Capture the correlation ID set during the request
            response["X-Inner-Correlation"] = get_correlation_id()
            return response

        return CorrelationIdMiddleware(get_response)

    def test_generates_correlation_id_when_absent(self) -> None:
        middleware = self._make_middleware()
        request = self.factory.get("/api/v1/records")
        response = middleware(request)

        # Response should have a correlation ID header
        assert CORRELATION_HEADER in response
        correlation_id = response[CORRELATION_HEADER]
        # Should be a valid UUID
        uuid.UUID(correlation_id)

    def test_propagates_incoming_correlation_id(self) -> None:
        middleware = self._make_middleware()
        incoming_id = "test-correlation-123"
        request = self.factory.get("/api/v1/records", HTTP_X_CORRELATION_ID=incoming_id)
        response = middleware(request)

        assert response[CORRELATION_HEADER] == incoming_id

    def test_correlation_id_available_in_context_during_request(self) -> None:
        middleware = self._make_middleware()
        incoming_id = "ctx-test-456"
        request = self.factory.get("/api/v1/records", HTTP_X_CORRELATION_ID=incoming_id)
        response = middleware(request)

        # The inner handler captured the correlation ID from context
        assert response["X-Inner-Correlation"] == incoming_id

    def test_correlation_id_set_on_request_object(self) -> None:
        captured_id = None

        def get_response(request: HttpRequest) -> HttpResponse:
            nonlocal captured_id
            captured_id = getattr(request, "correlation_id", None)
            return HttpResponse("OK")

        middleware = CorrelationIdMiddleware(get_response)
        incoming_id = "req-attr-789"
        request = self.factory.get("/api/v1/records", HTTP_X_CORRELATION_ID=incoming_id)
        middleware(request)

        assert captured_id == incoming_id

    def test_ignores_blank_correlation_id(self) -> None:
        middleware = self._make_middleware()
        request = self.factory.get("/api/v1/records", HTTP_X_CORRELATION_ID="   ")
        response = middleware(request)

        # Should generate a new UUID, not use blank
        correlation_id = response[CORRELATION_HEADER]
        assert correlation_id.strip() != ""
        uuid.UUID(correlation_id)


# ---------------------------------------------------------------------------
# Sanitization tests
# ---------------------------------------------------------------------------


class TestSanitizeValue:
    """Tests for value-level sanitization."""

    def test_redacts_email_addresses(self) -> None:
        result = sanitize_value("Contact user@example.com for info")
        assert "user@example.com" not in result
        assert REDACTED in result

    def test_redacts_capability_tokens(self) -> None:
        hex_token = "a" * 64
        result = sanitize_value(f"Token: {hex_token}")
        assert hex_token not in result
        assert REDACTED in result

    def test_redacts_password_assignments(self) -> None:
        canary = "super" + "_secret_123"
        result = sanitize_value(f"password={canary}")
        assert canary not in result
        assert REDACTED in result

    def test_redacts_aws_keys(self) -> None:
        # AWS documentation's example access key id, assembled at runtime so
        # this fixture is not mistaken for a committed credential.
        canary = "AKIA" + "IOSFODNN7EXAMPLE"
        result = sanitize_value(canary)
        assert canary not in result
        assert REDACTED in result

    def test_redacts_management_link_values(self) -> None:
        canary = "https://example.com/manage/" + "abc"
        result = sanitize_value(f"management_link= {canary}")
        assert canary not in result
        assert REDACTED in result

    def test_preserves_safe_strings(self) -> None:
        safe = "Processing record abc-123 in moderation queue"
        assert sanitize_value(safe) == safe

    def test_non_string_passes_through(self) -> None:
        assert sanitize_value(42) == 42
        assert sanitize_value(None) is None
        assert sanitize_value(True) is True


class TestSanitizeLogRecord:
    """Tests for allowlist-based log record sanitization."""

    def test_allows_only_allowlisted_fields(self) -> None:
        record = {
            "timestamp": "2024-01-01T00:00:00Z",
            "level": "INFO",
            "message": "Test message",
            "correlation_id": "abc-123",
            # These should be dropped
            "email": "secret@example.com",
            "evidence_content": b"binary data",
            "body": '{"password": "xxx"}',
            "stack_trace": "File ...",
            "raw_exception": "ValueError: ...",
        }
        sanitized = sanitize_log_record(record)

        # Allowed fields present
        assert "timestamp" in sanitized
        assert "level" in sanitized
        assert "message" in sanitized
        assert "correlation_id" in sanitized

        # Sensitive fields dropped
        assert "email" not in sanitized
        assert "evidence_content" not in sanitized
        assert "body" not in sanitized
        assert "stack_trace" not in sanitized
        assert "raw_exception" not in sanitized

    def test_sanitizes_values_in_allowed_fields(self) -> None:
        record = {
            "message": "User user@example.com submitted",
            "correlation_id": "safe-id",
        }
        sanitized = sanitize_log_record(record)
        assert "user@example.com" not in sanitized["message"]
        assert REDACTED in sanitized["message"]

    def test_empty_record_produces_empty_output(self) -> None:
        assert sanitize_log_record({}) == {}


# ---------------------------------------------------------------------------
# SanitizedAuditFailure tests (Req 7.12)
# ---------------------------------------------------------------------------


class TestSanitizedAuditFailure:
    """Tests for the sanitized audit failure event structure."""

    def test_contains_only_safe_fields(self) -> None:
        failure = SanitizedAuditFailure(
            actor_surrogate="staff-uuid-123",
            action="approve_record",
            target_surrogate="record-uuid-456",
            correlation_id="corr-789",
        )
        log_dict = failure.to_log_dict()

        # All fields should be in the allowed set
        for key in log_dict:
            assert key in ALLOWED_LOG_FIELDS, f"Field '{key}' not in allowlist"

        # Required fields present per Req 7.12
        assert log_dict["actor_surrogate"] == "staff-uuid-123"
        assert log_dict["action"] == "approve_record"
        assert log_dict["target_id"] == "record-uuid-456"
        assert log_dict["correlation_id"] == "corr-789"
        assert log_dict["error_category"] == "audit_write_failure"
        assert log_dict["level"] == "ERROR"

    def test_no_submitted_content_present(self) -> None:
        failure = SanitizedAuditFailure(
            actor_surrogate="x",
            action="y",
            target_surrogate="z",
            correlation_id="c",
        )
        log_dict = failure.to_log_dict()

        # Must not contain any content-bearing fields
        forbidden = {"body", "evidence", "email", "free_text", "capability"}
        assert not forbidden.intersection(log_dict.keys())


# ---------------------------------------------------------------------------
# Error classification tests
# ---------------------------------------------------------------------------


class TestErrorClassification:
    """Tests for safe error classification."""

    def test_classifies_known_exceptions(self) -> None:
        assert ErrorClassification.classify(ValueError()) == "internal_error"
        assert ErrorClassification.classify(TimeoutError()) == "service_unavailable"
        assert ErrorClassification.classify(ConnectionError()) == "service_unavailable"

    def test_unknown_exception_is_internal_error(self) -> None:
        class CustomError(Exception):
            pass

        assert ErrorClassification.classify(CustomError()) == "internal_error"

    def test_retryable_classification(self) -> None:
        assert ErrorClassification.is_retryable("service_unavailable") is True
        assert ErrorClassification.is_retryable("internal_error") is False
        assert ErrorClassification.is_retryable("validation_error") is False


# ---------------------------------------------------------------------------
# OperationalError tests (Req 16.5)
# ---------------------------------------------------------------------------


class TestOperationalError:
    """Tests for structured operational error logging."""

    def test_to_log_dict_contains_required_fields(self) -> None:
        error = OperationalError(
            timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
            correlation_id="op-corr-123",
            deployment_version="v1.2.3",
            operation="POST /api/v1/records",
            error_category="internal_error",
        )
        log_dict = error.to_log_dict()

        assert log_dict["correlation_id"] == "op-corr-123"
        assert log_dict["deployment_version"] == "v1.2.3"
        assert log_dict["operation"] == "POST /api/v1/records"
        assert log_dict["error_category"] == "internal_error"
        assert log_dict["level"] == "ERROR"

    def test_to_log_dict_all_fields_in_allowlist(self) -> None:
        error = OperationalError(
            timestamp=datetime(2024, 1, 15, tzinfo=UTC),
            correlation_id="x",
            deployment_version="v1",
            operation="op",
            error_category="cat",
        )
        for key in error.to_log_dict():
            assert key in ALLOWED_LOG_FIELDS


# ---------------------------------------------------------------------------
# Safe error translation middleware tests (Req 16.11)
# ---------------------------------------------------------------------------


class TestSafeErrorTranslationMiddleware:
    """Tests for the error translation middleware."""

    def setup_method(self) -> None:
        self.factory = RequestFactory()

    def _make_middleware(self, exc: Exception | None = None) -> SafeErrorTranslationMiddleware:
        def get_response(request: HttpRequest) -> HttpResponse:
            if exc:
                raise exc
            return HttpResponse("OK")

        return SafeErrorTranslationMiddleware(get_response)

    def test_passes_through_successful_responses(self) -> None:
        middleware = self._make_middleware()
        request = self.factory.get("/api/v1/records")
        response = middleware(request)
        assert response.status_code == 200

    @override_settings(DEPLOYMENT_VERSION="v2.0.0")
    def test_returns_500_for_api_unhandled_error(self) -> None:
        # Set up correlation ID first
        set_correlation_id("test-corr-500")
        middleware = self._make_middleware(RuntimeError("secret details"))
        request = self.factory.get("/api/v1/records")
        response = middleware(request)

        assert response.status_code == 500
        body = json.loads(response.content)
        assert body["type"] == "/problems/internal-error"
        assert body["correlation_id"] == "test-corr-500"
        # Must NOT contain exception details
        assert "secret details" not in response.content.decode()

    def test_returns_503_for_retryable_errors(self) -> None:
        set_correlation_id("test-corr-503")
        middleware = self._make_middleware(TimeoutError("connection timed out"))
        request = self.factory.get("/api/v1/records")
        response = middleware(request)

        assert response.status_code == 503
        body = json.loads(response.content)
        assert body["type"] == "/problems/service-unavailable"
        assert "connection timed out" not in response.content.decode()

    def test_reraises_for_html_requests(self) -> None:
        middleware = self._make_middleware(RuntimeError("boom"))
        request = self.factory.get("/browse/")
        with pytest.raises(RuntimeError):
            middleware(request)

    def test_never_leaks_exception_message(self) -> None:
        set_correlation_id("no-leak")
        secret_msg = "PostgreSQL connection to db-prod-3.internal failed"
        middleware = self._make_middleware(Exception(secret_msg))
        request = self.factory.get("/api/v1/records")
        response = middleware(request)

        content = response.content.decode()
        assert secret_msg not in content
        assert "db-prod-3" not in content


# ---------------------------------------------------------------------------
# StructuredJsonFormatter tests
# ---------------------------------------------------------------------------


class TestStructuredJsonFormatter:
    """Tests for the structured JSON log formatter."""

    def _make_record(self, msg: str = "test", level: int = logging.INFO) -> logging.LogRecord:
        return logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=None,
            exc_info=None,
        )

    def test_produces_valid_json(self) -> None:
        formatter = StructuredJsonFormatter(deployment_version="v1.0.0", environment="test")
        record = self._make_record("Hello world")
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "Hello world"
        assert parsed["deployment_version"] == "v1.0.0"
        assert parsed["environment"] == "test"
        assert parsed["level"] == "INFO"

    def test_includes_correlation_id(self) -> None:
        set_correlation_id("fmt-corr-test")
        formatter = StructuredJsonFormatter()
        record = self._make_record()
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["correlation_id"] == "fmt-corr-test"

    def test_sanitizes_email_in_message(self) -> None:
        formatter = StructuredJsonFormatter()
        record = self._make_record("Error for user@secret.com")
        output = formatter.format(record)
        assert "user@secret.com" not in output
        assert REDACTED in output

    def test_excludes_exception_traceback(self) -> None:
        formatter = StructuredJsonFormatter()
        try:
            raise ValueError("secret internal state")
        except ValueError:
            import sys

            record = self._make_record("Error occurred")
            record.exc_info = sys.exc_info()

        output = formatter.format(record)
        assert "secret internal state" not in output
        assert "Traceback" not in output
        parsed = json.loads(output)
        assert parsed.get("error_category") == "ValueError"

    def test_only_allowlisted_fields_in_output(self) -> None:
        formatter = StructuredJsonFormatter()
        record = self._make_record()
        output = formatter.format(record)
        parsed = json.loads(output)
        for key in parsed:
            assert key in ALLOWED_LOG_FIELDS, f"Unexpected field '{key}' in output"


# ---------------------------------------------------------------------------
# SanitizingFilter tests
# ---------------------------------------------------------------------------


class TestSanitizingFilter:
    """Tests for the logging filter that sanitizes before formatting."""

    def test_redacts_sensitive_message(self) -> None:
        filt = SanitizingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Login password=hunter2 failed",
            args=None,
            exc_info=None,
        )
        filt.filter(record)
        assert "hunter2" not in record.msg
        assert REDACTED in record.msg

    def test_redacts_dict_args(self) -> None:
        filt = SanitizingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="%(email)s logged in",
            args=None,
            exc_info=None,
        )
        # Manually set dict args (as logging does internally)
        record.args = {"email": "admin@company.com"}
        filt.filter(record)
        assert isinstance(record.args, dict)
        assert "admin@company.com" not in str(record.args["email"])

    def test_redacts_tuple_args(self) -> None:
        filt = SanitizingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Value: %s",
            args=None,
            exc_info=None,
        )
        # Manually set tuple args - uses password= pattern which is detected
        record.args = ("password= abc123xyz",)
        filt.filter(record)
        assert isinstance(record.args, tuple)
        assert "abc123xyz" not in str(record.args[0])


# ---------------------------------------------------------------------------
# Integration: generate_correlation_id
# ---------------------------------------------------------------------------


class TestGenerateCorrelationId:
    """Tests for correlation ID generation."""

    def test_generates_valid_uuid(self) -> None:
        cid = generate_correlation_id()
        uuid.UUID(cid)  # Raises if invalid

    def test_generates_unique_ids(self) -> None:
        ids = {generate_correlation_id() for _ in range(100)}
        assert len(ids) == 100
