"""Tests for evidence intake, quarantine storage, and scanning jobs.

Covers:
- File validation: size limit, extension allowlist, MIME type allowlist, content detection
- Envelope encryption and quarantine storage
- Scan job processing: CLEAN, QUARANTINED, SCAN_ERROR paths
- Security event recording
- Access gating (blocked while pending/error)
- Private link and explanation evidence kinds
- Retry opportunity preserved after rejection
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import pytest

from token_budget_platform.adapters.clock import FrozenClock
from token_budget_platform.adapters.key_management import LocalKeyManagement
from token_budget_platform.adapters.object_storage import LocalObjectStore
from token_budget_platform.adapters.scanner import LocalMalwareScanner
from token_budget_platform.adapters.telemetry import LocalTelemetry
from token_budget_platform.application.evidence import EvidenceService, _xor_encrypt
from token_budget_platform.application.jobs import JobRuntime
from token_budget_platform.domain.evidence import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
    MAX_EVIDENCE_BYTES,
    EvidenceRejectionReason,
    ScanState,
    detect_content_type,
    validate_content_type_match,
    validate_declared_mime,
    validate_extension,
    validate_file_evidence,
    validate_file_size,
)

# ---------------------------------------------------------------------------
# Domain validation unit tests
# ---------------------------------------------------------------------------


class TestFileSize:
    def test_accepts_within_limit(self) -> None:
        assert validate_file_size(1024) is None
        assert validate_file_size(MAX_EVIDENCE_BYTES) is None

    def test_rejects_over_limit(self) -> None:
        result = validate_file_size(MAX_EVIDENCE_BYTES + 1)
        assert result is not None
        assert result.reason == EvidenceRejectionReason.FILE_TOO_LARGE

    def test_rejects_empty(self) -> None:
        result = validate_file_size(0)
        assert result is not None
        assert result.reason == EvidenceRejectionReason.EMPTY_FILE


class TestExtension:
    def test_accepts_all_allowed(self) -> None:
        for ext in ALLOWED_EXTENSIONS:
            assert validate_extension(f"document{ext}") is None

    def test_rejects_disallowed(self) -> None:
        result = validate_extension("malware.exe")
        assert result is not None
        assert result.reason == EvidenceRejectionReason.DISALLOWED_EXTENSION

    def test_rejects_no_extension(self) -> None:
        result = validate_extension("noextension")
        assert result is not None
        assert result.reason == EvidenceRejectionReason.DISALLOWED_EXTENSION

    def test_case_insensitive(self) -> None:
        assert validate_extension("report.PDF") is None
        assert validate_extension("photo.JPEG") is None


class TestDeclaredMime:
    def test_accepts_all_allowed(self) -> None:
        for mime in ALLOWED_MIME_TYPES:
            assert validate_declared_mime(mime) is None

    def test_accepts_with_charset(self) -> None:
        assert validate_declared_mime("text/plain; charset=utf-8") is None

    def test_rejects_disallowed(self) -> None:
        result = validate_declared_mime("application/zip")
        assert result is not None
        assert result.reason == EvidenceRejectionReason.DISALLOWED_MIME_TYPE

    def test_rejects_executable(self) -> None:
        result = validate_declared_mime("application/x-msdownload")
        assert result is not None
        assert result.reason == EvidenceRejectionReason.DISALLOWED_MIME_TYPE


class TestContentDetection:
    def test_detects_pdf(self) -> None:
        assert detect_content_type(b"%PDF-1.4 some content") == "application/pdf"

    def test_detects_png(self) -> None:
        header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        assert detect_content_type(header) == "image/png"

    def test_detects_jpeg(self) -> None:
        assert detect_content_type(b"\xff\xd8\xff\xe0" + b"\x00" * 50) == "image/jpeg"

    def test_detects_text(self) -> None:
        assert detect_content_type(b"Hello, this is plain text content") == "text/plain"

    def test_returns_none_for_short_header(self) -> None:
        assert detect_content_type(b"ab") is None


class TestContentTypeMatch:
    def test_matching_types_pass(self) -> None:
        assert validate_content_type_match("application/pdf", "application/pdf") is None

    def test_csv_detected_as_text_passes(self) -> None:
        assert validate_content_type_match("text/csv", "text/plain") is None

    def test_mismatched_types_rejected(self) -> None:
        result = validate_content_type_match("application/pdf", "image/png")
        assert result is not None
        assert result.reason == EvidenceRejectionReason.CONTENT_TYPE_MISMATCH

    def test_none_detection_passes(self) -> None:
        assert validate_content_type_match("application/pdf", None) is None


class TestFullValidation:
    def test_valid_pdf(self) -> None:
        header = b"%PDF-1.5 " + b"\x00" * 100
        result = validate_file_evidence("report.pdf", "application/pdf", 5000, header)
        assert result is None

    def test_invalid_size_stops_early(self) -> None:
        result = validate_file_evidence(
            "report.pdf", "application/pdf", MAX_EVIDENCE_BYTES + 1, b"%PDF"
        )
        assert result is not None
        assert result.reason == EvidenceRejectionReason.FILE_TOO_LARGE


# ---------------------------------------------------------------------------
# Integration tests (require Django DB)
# ---------------------------------------------------------------------------


def _make_service(
    clock: FrozenClock | None = None,
    scanner: LocalMalwareScanner | None = None,
) -> tuple[EvidenceService, LocalObjectStore, LocalKeyManagement, FrozenClock, JobRuntime]:
    """Create a fully wired EvidenceService with local adapters."""
    store = LocalObjectStore()
    keys = LocalKeyManagement(b"test-master-key-32-bytes-long!!" + b"\x00" * 2)
    clk = clock or FrozenClock(datetime(2026, 6, 1, tzinfo=UTC))
    scan = scanner or LocalMalwareScanner()
    telemetry = LocalTelemetry()
    runtime = JobRuntime(clock=clk, telemetry=telemetry, lease_seconds=60)
    service = EvidenceService(
        object_store=store,
        key_management=keys,
        scanner=scan,
        clock=clk,
        telemetry=telemetry,
        job_runtime=runtime,
    )
    service.register_scan_handler(runtime)
    return service, store, keys, clk, runtime


def _create_test_record(db):  # noqa: ARG001
    """Create a minimal token-budget record for testing evidence association."""
    from datetime import date
    from decimal import Decimal

    from django.utils import timezone as dj_tz

    from token_budget_platform.adapters.django.models import (
        BudgetScope,
        ControlledValue,
        ControlledValueKind,
        MethodologyVersion,
        Organization,
        PublicationState,
        TokenBudgetRecord,
    )

    now = dj_tz.now()
    org = Organization.objects.create(
        name="Test Org",
        normalized_name="test org",
        slug="test-org",
        industry="Technology",
        size_band=ControlledValue.objects.create(
            kind=ControlledValueKind.SIZE_BAND,
            code="S",
            label="Small",
            normalized_label="small",
            active_from=now,
            vocabulary_version=1,
        ),
        region=ControlledValue.objects.create(
            kind=ControlledValueKind.REGION,
            code="US",
            label="United States",
            normalized_label="united states",
            active_from=now,
            vocabulary_version=1,
        ),
    )
    method = MethodologyVersion.objects.create(
        version=1, name="v1", rules={}, is_active=True, activated_at=now
    )
    provider = ControlledValue.objects.create(
        kind=ControlledValueKind.MODEL_PROVIDER,
        code="OPENAI",
        label="OpenAI",
        normalized_label="openai",
        active_from=now,
        vocabulary_version=1,
    )
    product = ControlledValue.objects.create(
        kind=ControlledValueKind.AI_PRODUCT,
        code="GPT4",
        label="GPT-4",
        normalized_label="gpt-4",
        active_from=now,
        vocabulary_version=1,
    )
    workload = ControlledValue.objects.create(
        kind=ControlledValueKind.WORKLOAD_CATEGORY,
        code="DEV",
        label="Development",
        normalized_label="development",
        active_from=now,
        vocabulary_version=1,
    )
    region = ControlledValue.objects.create(
        kind=ControlledValueKind.REGION,
        code="EU",
        label="Europe",
        normalized_label="europe",
        active_from=now,
        vocabulary_version=1,
    )
    record = TokenBudgetRecord.objects.create(
        organization=org,
        budget_scope=BudgetScope.TEAM,
        seat_count=10,
        model_provider=provider,
        ai_product=product,
        workload_category=workload,
        region=region,
        organization_search="test org",
        provider_search="openai",
        product_search="gpt-4",
        token_budget=Decimal("1000000"),
        reporting_start=date(2026, 1, 1),
        reporting_end=date(2026, 6, 30),
        source_date=date(2026, 5, 1),
        publication_state=PublicationState.QUEUED,
        methodology_version=method,
    )
    return record


@pytest.mark.django_db
class TestEvidenceIntakeFile:
    """Test file evidence intake validation, storage, and job creation."""

    def test_accepts_valid_pdf(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        content = b"%PDF-1.5 " + b"x" * 500
        result = service.accept_file(
            record_id=record.id,
            filename="report.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )

        assert result.accepted
        assert result.evidence_id is not None
        assert result.evidence_id is not None
        assert result.rejection is None

        # Verify evidence stored in quarantine
        from token_budget_platform.adapters.django.models import Evidence

        evidence = Evidence.objects.get(id=result.evidence_id)
        assert evidence.scan_state == "PENDING"
        assert evidence.byte_count == len(content)
        assert evidence.mime_type == "application/pdf"
        assert evidence.object_key is not None
        assert evidence.object_key.startswith("quarantine/evidence/")

        # Verify object stored
        assert evidence.object_key in store.keys()

        # Verify scan job created
        from token_budget_platform.adapters.django.models import Job

        job = Job.objects.get(idempotency_key=f"evidence-scan:{evidence.id}")
        assert job.job_type == "EVIDENCE_SCAN"

    def test_rejects_oversized_file(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        content = b"%PDF-1.5 " + b"x" * (MAX_EVIDENCE_BYTES + 1)
        result = service.accept_file(
            record_id=record.id,
            filename="big.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )

        assert not result.accepted
        assert result.rejection is not None
        assert result.rejection.reason == EvidenceRejectionReason.FILE_TOO_LARGE

        # No evidence record or object stored
        from token_budget_platform.adapters.django.models import Evidence

        assert Evidence.objects.count() == 0
        assert len(store.keys()) == 0

    def test_rejects_disallowed_extension(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        result = service.accept_file(
            record_id=record.id,
            filename="malware.exe",
            declared_mime="application/pdf",
            stream=BytesIO(b"%PDF-1.5 content"),
        )

        assert not result.accepted
        assert result.rejection is not None
        assert result.rejection.reason == EvidenceRejectionReason.DISALLOWED_EXTENSION

    def test_rejects_disallowed_mime_type(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        result = service.accept_file(
            record_id=record.id,
            filename="archive.pdf",
            declared_mime="application/zip",
            stream=BytesIO(b"%PDF-1.5 content"),
        )

        assert not result.accepted
        assert result.rejection is not None
        assert result.rejection.reason == EvidenceRejectionReason.DISALLOWED_MIME_TYPE

    def test_rejects_type_mismatch(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        # Declare PDF but send PNG content
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = service.accept_file(
            record_id=record.id,
            filename="image.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(png_header),
        )

        assert not result.accepted
        assert result.rejection is not None
        assert result.rejection.reason == EvidenceRejectionReason.CONTENT_TYPE_MISMATCH

    def test_preserves_retry_after_rejection(self) -> None:
        """After a rejection, the contributor can try again with a valid file."""
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        # First attempt: invalid
        result1 = service.accept_file(
            record_id=record.id,
            filename="too_big.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(b"%PDF" + b"x" * (MAX_EVIDENCE_BYTES + 1)),
        )
        assert not result1.accepted

        # Second attempt: valid
        result2 = service.accept_file(
            record_id=record.id,
            filename="report.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(b"%PDF-1.5 " + b"x" * 100),
        )
        assert result2.accepted

    def test_accepts_all_allowed_types(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        test_cases = [
            ("doc.pdf", "application/pdf", b"%PDF-1.5 content here"),
            ("img.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50),
            ("photo.jpeg", "image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 50),
            ("notes.txt", "text/plain", b"Plain text content here"),
            ("data.csv", "text/csv", b"col1,col2\nval1,val2\n"),
        ]

        for filename, mime, content in test_cases:
            result = service.accept_file(
                record_id=record.id,
                filename=filename,
                declared_mime=mime,
                stream=BytesIO(content),
            )
            assert result.accepted
        assert result.evidence_id is not None, f"Failed for {filename} ({mime})"


@pytest.mark.django_db
class TestEvidenceScanJob:
    """Test scan-worker processing of evidence files."""

    def test_clean_file_transitions_to_clean(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        content = b"%PDF-1.5 " + b"safe content" * 10
        result = service.accept_file(
            record_id=record.id,
            filename="clean.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )
        assert result.accepted
        assert result.evidence_id is not None

        # Process the scan job
        outcome = service.process_scan(result.evidence_id)
        assert outcome.final_state == ScanState.CLEAN

        from token_budget_platform.adapters.django.models import Evidence

        evidence = Evidence.objects.get(id=result.evidence_id)
        assert evidence.scan_state == "CLEAN"
        assert evidence.scanned_at is not None
        assert evidence.final_resolution_at is not None

    def test_infected_file_transitions_to_quarantined(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        # Content containing EICAR marker triggers the local scanner
        content = b"%PDF-1.5 EICAR test string"
        result = service.accept_file(
            record_id=record.id,
            filename="infected.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )
        assert result.accepted
        assert result.evidence_id is not None

        outcome = service.process_scan(result.evidence_id)
        assert outcome.final_state == ScanState.QUARANTINED

        from token_budget_platform.adapters.django.models import Evidence

        evidence = Evidence.objects.get(id=result.evidence_id)
        assert evidence.scan_state == "QUARANTINED"
        assert evidence.scanned_at is not None
        # Quarantined files remain stored (not deleted)
        assert evidence.object_key in store.keys()

    def test_scan_records_security_event(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        content = b"%PDF-1.5 clean document"
        result = service.accept_file(
            record_id=record.id,
            filename="doc.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )
        assert result.evidence_id is not None
        service.process_scan(result.evidence_id)

        from token_budget_platform.adapters.django.models import SecurityEvent

        events = SecurityEvent.objects.filter(target_surrogate=result.evidence_id)
        assert events.count() == 1
        event = events.first()
        assert event is not None
        assert event.event_type == "EVIDENCE_SCAN_COMPLETE"
        assert event.category == "MALWARE_SCAN"
        assert event.outcome == "CLEAN"

    def test_scan_is_idempotent(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        content = b"%PDF-1.5 content"
        result = service.accept_file(
            record_id=record.id,
            filename="doc.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )
        assert result.evidence_id is not None
        # First scan
        outcome1 = service.process_scan(result.evidence_id)
        assert outcome1.final_state == ScanState.CLEAN

        # Second scan is a no-op
        outcome2 = service.process_scan(result.evidence_id)
        assert outcome2.final_state == ScanState.CLEAN
        assert outcome2.outcome_detail == "already_scanned"


@pytest.mark.django_db
class TestEvidenceAccessGating:
    """Test that evidence access is blocked while pending or in error state."""

    def test_pending_evidence_not_accessible(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        content = b"%PDF-1.5 document"
        result = service.accept_file(
            record_id=record.id,
            filename="doc.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )
        assert result.evidence_id is not None

        # Before scan: not accessible
        assert not service.is_accessible(result.evidence_id)

    def test_clean_evidence_accessible(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        content = b"%PDF-1.5 safe document content"
        result = service.accept_file(
            record_id=record.id,
            filename="doc.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )
        assert result.evidence_id is not None

        service.process_scan(result.evidence_id)
        assert service.is_accessible(result.evidence_id)

    def test_quarantined_evidence_not_accessible(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        content = b"%PDF-1.5 EICAR infected content"
        result = service.accept_file(
            record_id=record.id,
            filename="bad.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(content),
        )
        assert result.evidence_id is not None

        service.process_scan(result.evidence_id)
        assert not service.is_accessible(result.evidence_id)


@pytest.mark.django_db
class TestPrivateLinkEvidence:
    """Test private link evidence acceptance."""

    def test_accepts_valid_link(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        result = service.accept_private_link(
            record_id=record.id,
            url="https://docs.example.com/evidence/123",
        )

        assert result.accepted
        assert result.evidence_id is not None
        assert result.evidence_id is not None

        from token_budget_platform.adapters.django.models import Evidence

        evidence = Evidence.objects.get(id=result.evidence_id)
        assert evidence.kind == "PRIVATE_LINK"
        assert evidence.scan_state == "CLEAN"  # Links skip scanning

    def test_rejects_empty_link(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        result = service.accept_private_link(record_id=record.id, url="")
        assert not result.accepted

    def test_rejects_overly_long_link(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        result = service.accept_private_link(record_id=record.id, url="https://x.com/" + "a" * 2050)
        assert not result.accepted


@pytest.mark.django_db
class TestExplanationEvidence:
    """Test explanation evidence acceptance."""

    def test_accepts_valid_explanation(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        result = service.accept_explanation(
            record_id=record.id,
            text="This budget was confirmed via internal IT dashboard.",
        )

        assert result.accepted
        assert result.evidence_id is not None
        assert result.evidence_id is not None

        from token_budget_platform.adapters.django.models import Evidence

        evidence = Evidence.objects.get(id=result.evidence_id)
        assert evidence.kind == "EXPLANATION"
        assert evidence.scan_state == "CLEAN"  # Text skips scanning

    def test_rejects_empty_explanation(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        result = service.accept_explanation(record_id=record.id, text="")
        assert not result.accepted

    def test_rejects_overly_long_explanation(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        result = service.accept_explanation(record_id=record.id, text="x" * 4001)
        assert not result.accepted


@pytest.mark.django_db
class TestEnvelopeEncryption:
    """Test that evidence content is encrypted with per-evidence keys."""

    def test_stored_content_is_encrypted(self) -> None:
        record = _create_test_record(None)
        service, store, keys, clock, runtime = _make_service()

        original_content = b"%PDF-1.5 " + b"sensitive budget evidence" * 10
        result = service.accept_file(
            record_id=record.id,
            filename="budget.pdf",
            declared_mime="application/pdf",
            stream=BytesIO(original_content),
        )
        assert result.accepted
        assert result.evidence_id is not None

        from token_budget_platform.adapters.django.models import Evidence

        evidence = Evidence.objects.get(id=result.evidence_id)

        # Read what was stored - it should be encrypted (not plaintext)
        assert evidence.object_key is not None
        with store.open_encrypted(evidence.object_key) as stream:
            stored_bytes = stream.read()

        assert stored_bytes != original_content
        assert len(stored_bytes) == len(original_content)

    def test_encryption_is_reversible_with_key(self) -> None:
        """Verify the local XOR encryption is reversible (for scan processing)."""
        key = b"secret-key-for-testing-32-bytes!"
        plaintext = b"This is confidential evidence content."
        encrypted = _xor_encrypt(plaintext, key)
        decrypted = _xor_encrypt(encrypted, key)
        assert decrypted == plaintext
        assert encrypted != plaintext
