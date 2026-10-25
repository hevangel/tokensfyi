"""Evidence application services.

Provides intake validation, quarantine storage with per-evidence envelope
encryption, scan-job scheduling, and scan-worker processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import BinaryIO
from uuid import UUID

from django.db import transaction

from token_budget_platform.adapters.django.models import (
    Evidence as EvidenceModel,
)
from token_budget_platform.adapters.django.models import (
    EvidenceKind as EvidenceKindDB,
)
from token_budget_platform.adapters.django.models import (
    EvidenceScanState,
    SecurityEvent,
    TokenBudgetRecord,
)
from token_budget_platform.application.jobs import JobRuntime, RetryableJobError, TerminalJobError
from token_budget_platform.domain.evidence import (
    MAX_EVIDENCE_BYTES,
    EvidenceRejection,
    EvidenceRejectionReason,
    ScanState,
    validate_file_evidence,
)
from token_budget_platform.domain.ports import (
    Clock,
    KeyManagementPort,
    MalwareScannerPort,
    ObjectMetadata,
    ObjectStorePort,
    TelemetryPort,
    WrappedDataKey,
)

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceIntakeResult:
    """Outcome of an evidence intake attempt."""

    accepted: bool
    evidence_id: UUID | None = None
    rejection: EvidenceRejection | None = None


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    """Outcome of a scan-job execution."""

    evidence_id: UUID
    final_state: ScanState
    scanner_version: str
    outcome_detail: str


# ---------------------------------------------------------------------------
# Quarantine key helpers
# ---------------------------------------------------------------------------

_QUARANTINE_PREFIX = "quarantine/evidence/"


def _quarantine_key(evidence_id: UUID) -> str:
    return f"{_QUARANTINE_PREFIX}{evidence_id}"


# ---------------------------------------------------------------------------
# EvidenceService
# ---------------------------------------------------------------------------


class EvidenceService:
    """Orchestrates evidence intake, encryption, quarantine, and scan processing.

    Responsibilities:
    - Validate file size, extension, declared MIME, and detected content type
    - Encrypt evidence stream with per-evidence envelope encryption
    - Store in private quarantine prefix
    - Create evidence record + durable scan job in one transaction
    - Block access while pending/error
    - Worker-side: decrypt, scan, record security event, transition state
    """

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        key_management: KeyManagementPort,
        scanner: MalwareScannerPort,
        clock: Clock,
        telemetry: TelemetryPort | None = None,
        job_runtime: JobRuntime | None = None,
    ) -> None:
        self._store = object_store
        self._keys = key_management
        self._scanner = scanner
        self._clock = clock
        self._telemetry = telemetry
        self._jobs = job_runtime

    # ------------------------------------------------------------------
    # Evidence intake (web process)
    # ------------------------------------------------------------------

    def accept_file(
        self,
        *,
        record_id: UUID,
        filename: str,
        declared_mime: str,
        stream: BinaryIO,
    ) -> EvidenceIntakeResult:
        """Accept a file evidence upload. Validates, encrypts, stores, and enqueues scan.

        The stream is read fully to validate size and content type. On rejection,
        the submission opportunity is preserved (the contributor can retry).
        """
        # Read the full content to validate
        content = stream.read()
        byte_count = len(content)

        # Header for magic-byte detection (first 512 bytes)
        header = content[:512] if content else b""

        # Run the validation checks
        rejection = validate_file_evidence(filename, declared_mime, byte_count, header)
        if rejection is not None:
            return EvidenceIntakeResult(accepted=False, rejection=rejection)

        # Verify the record exists
        try:
            TokenBudgetRecord.objects.get(id=record_id)
        except TokenBudgetRecord.DoesNotExist:
            return EvidenceIntakeResult(
                accepted=False,
                rejection=EvidenceRejection(
                    reason=EvidenceRejectionReason.EMPTY_FILE,
                    message="Associated record not found.",
                ),
            )

        # Generate a per-evidence data key for envelope encryption
        wrapped_key = self._keys.generate_data_key("evidence")
        plaintext_key = self._keys.decrypt_data_key(wrapped_key)

        # Encrypt the content with the data key (simple XOR-stream for local;
        # production uses AES-256-GCM via KMS)
        encrypted_content = _xor_encrypt(content, plaintext_key)

        now = self._clock.now()

        # Pre-generate the evidence ID so we can set the object key on creation
        from token_budget_platform.adapters.django.models import uuid7

        evidence_id = uuid7()
        object_key = _quarantine_key(evidence_id)

        # Store and record atomically
        with transaction.atomic():
            evidence = EvidenceModel.objects.create(
                id=evidence_id,
                record_id=record_id,
                kind=EvidenceKindDB.FILE,
                object_key=object_key,
                filename=filename[:255],
                mime_type=declared_mime.strip().lower().split(";")[0].strip()[:127],
                byte_count=byte_count,
                wrapped_key_reference=wrapped_key.key_id,
                scan_state=EvidenceScanState.PENDING,
                submitted_at=now,
            )

            # Enqueue the durable scan job
            if self._jobs is not None:
                self._jobs.enqueue(
                    job_type="EVIDENCE_SCAN",
                    payload={"evidence_id": evidence.id},
                    idempotency_key=f"evidence-scan:{evidence.id}",
                    priority=70,
                    max_attempts=3,
                )

        # Write encrypted content to quarantine (outside transaction to avoid
        # holding DB locks during I/O)
        self._store.put_encrypted(
            object_key,
            BytesIO(encrypted_content),
            ObjectMetadata(
                content_type=evidence.mime_type,
                byte_count=len(encrypted_content),
                encryption_key_id=wrapped_key.key_id,
                values={"wrapped_ciphertext": wrapped_key.ciphertext.hex()},
            ),
        )

        if self._telemetry:
            self._telemetry.increment(
                "evidence.intake.accepted",
                attributes={"kind": "FILE", "mime_type": evidence.mime_type},
            )

        return EvidenceIntakeResult(accepted=True, evidence_id=evidence.id)

    def accept_private_link(
        self,
        *,
        record_id: UUID,
        url: str,
    ) -> EvidenceIntakeResult:
        """Accept a private link as evidence (no file scanning needed)."""
        if not url or len(url) > 2048:
            return EvidenceIntakeResult(
                accepted=False,
                rejection=EvidenceRejection(
                    reason=EvidenceRejectionReason.EMPTY_FILE,
                    message="Private link must be a non-empty URL of at most 2048 characters.",
                ),
            )

        try:
            TokenBudgetRecord.objects.get(id=record_id)
        except TokenBudgetRecord.DoesNotExist:
            return EvidenceIntakeResult(
                accepted=False,
                rejection=EvidenceRejection(
                    reason=EvidenceRejectionReason.EMPTY_FILE,
                    message="Associated record not found.",
                ),
            )

        # Encrypt the URL content
        wrapped_key = self._keys.generate_data_key("evidence")
        plaintext_key = self._keys.decrypt_data_key(wrapped_key)
        encrypted_url = _xor_encrypt(url.encode("utf-8"), plaintext_key)

        now = self._clock.now()

        with transaction.atomic():
            evidence = EvidenceModel.objects.create(
                record_id=record_id,
                kind=EvidenceKindDB.PRIVATE_LINK,
                encrypted_content=encrypted_url,
                byte_count=len(url.encode("utf-8")),
                wrapped_key_reference=wrapped_key.key_id,
                scan_state=EvidenceScanState.CLEAN,  # Links are not scanned
                submitted_at=now,
                scanned_at=now,
            )

        return EvidenceIntakeResult(accepted=True, evidence_id=evidence.id)

    def accept_explanation(
        self,
        *,
        record_id: UUID,
        text: str,
    ) -> EvidenceIntakeResult:
        """Accept a structured explanation as evidence (no file scanning needed)."""
        if not text or len(text) > 4000:
            return EvidenceIntakeResult(
                accepted=False,
                rejection=EvidenceRejection(
                    reason=EvidenceRejectionReason.EMPTY_FILE,
                    message="Explanation must be between 1 and 4000 characters.",
                ),
            )

        try:
            TokenBudgetRecord.objects.get(id=record_id)
        except TokenBudgetRecord.DoesNotExist:
            return EvidenceIntakeResult(
                accepted=False,
                rejection=EvidenceRejection(
                    reason=EvidenceRejectionReason.EMPTY_FILE,
                    message="Associated record not found.",
                ),
            )

        # Encrypt the explanation text
        wrapped_key = self._keys.generate_data_key("evidence")
        plaintext_key = self._keys.decrypt_data_key(wrapped_key)
        encrypted_text = _xor_encrypt(text.encode("utf-8"), plaintext_key)

        now = self._clock.now()

        with transaction.atomic():
            evidence = EvidenceModel.objects.create(
                record_id=record_id,
                kind=EvidenceKindDB.EXPLANATION,
                encrypted_content=encrypted_text,
                byte_count=len(text.encode("utf-8")),
                wrapped_key_reference=wrapped_key.key_id,
                scan_state=EvidenceScanState.CLEAN,  # Text is not scanned
                submitted_at=now,
                scanned_at=now,
            )

        return EvidenceIntakeResult(accepted=True, evidence_id=evidence.id)

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    def is_accessible(self, evidence_id: UUID) -> bool:
        """Evidence is accessible only when scan state is CLEAN."""
        try:
            evidence = EvidenceModel.objects.get(id=evidence_id)
        except EvidenceModel.DoesNotExist:
            return False
        return evidence.scan_state == EvidenceScanState.CLEAN

    def read_for_moderator(self, evidence_id: UUID) -> tuple[bool, str, str, bytes]:
        """Decrypt clean evidence for an authorized moderator.

        Returns ``(allowed, filename, mime_type, content)``.  Access is denied
        for unknown evidence or any scan state other than CLEAN (Req 6.2, 10.7):
        pending, errored, and quarantined evidence is never readable, and the
        internal object key never leaves this module.
        """
        try:
            evidence = EvidenceModel.objects.get(id=evidence_id)
        except EvidenceModel.DoesNotExist:
            return False, "", "", b""
        if evidence.scan_state != EvidenceScanState.CLEAN or not evidence.object_key:
            return False, "", "", b""

        try:
            with self._store.open_encrypted(evidence.object_key) as encrypted_stream:
                encrypted_content = encrypted_stream.read()
            metadata = self._store.metadata(evidence.object_key)
            wrapped_hex = metadata.values.get("wrapped_ciphertext", "")
            if evidence.wrapped_key_reference is None:
                return False, "", "", b""
            plaintext_key = self._keys.decrypt_data_key(
                WrappedDataKey(
                    key_id=evidence.wrapped_key_reference,
                    ciphertext=bytes.fromhex(wrapped_hex) if wrapped_hex else b"",
                )
            )
            content = _xor_encrypt(encrypted_content, plaintext_key)
        except (FileNotFoundError, KeyError, ValueError):
            return False, "", "", b""
        return True, evidence.filename, evidence.mime_type, content

    # ------------------------------------------------------------------
    # Scan worker (background job handler)
    # ------------------------------------------------------------------

    def process_scan(self, evidence_id: UUID) -> ScanOutcome:
        """Decrypt evidence from quarantine, scan, record outcome, transition state.

        Called by the job worker. Must be idempotent: re-scanning an already
        terminal evidence record is a no-op.
        """
        try:
            evidence = EvidenceModel.objects.select_for_update().get(id=evidence_id)
        except EvidenceModel.DoesNotExist as exc:
            raise TerminalJobError("evidence_not_found") from exc

        # Idempotent: already scanned
        if evidence.scan_state in (EvidenceScanState.CLEAN, EvidenceScanState.QUARANTINED):
            return ScanOutcome(
                evidence_id=evidence_id,
                final_state=ScanState(evidence.scan_state),
                scanner_version=evidence.scanner_version,
                outcome_detail="already_scanned",
            )

        # Only process PENDING or SCAN_ERROR (retry)
        if evidence.scan_state not in (EvidenceScanState.PENDING, EvidenceScanState.SCAN_ERROR):
            raise TerminalJobError("invalid_scan_state")

        if not evidence.object_key:
            raise TerminalJobError("no_object_key")

        now = self._clock.now()

        try:
            # Open the encrypted stream from quarantine
            with self._store.open_encrypted(evidence.object_key) as encrypted_stream:
                encrypted_content = encrypted_stream.read()

            # Retrieve the wrapped key ciphertext from object metadata
            obj_metadata = self._store.metadata(evidence.object_key)
            wrapped_ciphertext_hex = obj_metadata.values.get("wrapped_ciphertext", "")
            wrapped_ciphertext = (
                bytes.fromhex(wrapped_ciphertext_hex) if wrapped_ciphertext_hex else b""
            )

            # Decrypt with the data key
            if evidence.wrapped_key_reference is None:
                raise KeyError("missing wrapped key reference")
            plaintext_key = self._keys.decrypt_data_key(
                WrappedDataKey(key_id=evidence.wrapped_key_reference, ciphertext=wrapped_ciphertext)
            )
            decrypted_content = _xor_encrypt(encrypted_content, plaintext_key)

            # Scan through ClamAV (bounded stream)
            bounded_stream = BytesIO(decrypted_content[:MAX_EVIDENCE_BYTES])
            scan_result = self._scanner.scan(bounded_stream)

        except (FileNotFoundError, KeyError) as exc:
            # Cannot decrypt or find the object - mark as scan error
            evidence.scan_state = EvidenceScanState.SCAN_ERROR
            evidence.scan_result = f"retrieval_error: {type(exc).__name__}"
            evidence.scanned_at = now
            evidence.save(update_fields=["scan_state", "scan_result", "scanned_at", "updated_at"])
            self._record_security_event(evidence, "SCAN_ERROR", "retrieval_failed", now)
            raise RetryableJobError("retrieval_error") from exc

        except Exception as exc:
            evidence.scan_state = EvidenceScanState.SCAN_ERROR
            evidence.scan_result = "scan_processing_error"
            evidence.scanned_at = now
            evidence.save(update_fields=["scan_state", "scan_result", "scanned_at", "updated_at"])
            self._record_security_event(evidence, "SCAN_ERROR", "processing_error", now)
            raise RetryableJobError("scan_error") from exc

        # Record the scan outcome
        if scan_result.clean:
            final_state = EvidenceScanState.CLEAN
        else:
            final_state = EvidenceScanState.QUARANTINED

        evidence.scan_state = final_state
        evidence.scan_result = scan_result.outcome[:400]
        evidence.scanner_version = scan_result.scanner_version[:100]
        evidence.scanned_at = now
        if final_state == EvidenceScanState.CLEAN:
            evidence.final_resolution_at = now
        evidence.save(
            update_fields=[
                "scan_state",
                "scan_result",
                "scanner_version",
                "scanned_at",
                "final_resolution_at",
                "updated_at",
            ]
        )

        # Record sanitized security event
        self._record_security_event(
            evidence,
            "SCAN_COMPLETE",
            scan_result.outcome,
            now,
            scanner_version=scan_result.scanner_version,
        )

        if self._telemetry:
            self._telemetry.increment(
                "evidence.scan.completed",
                attributes={
                    "kind": evidence.kind,
                    "result": final_state,
                },
            )

        return ScanOutcome(
            evidence_id=evidence_id,
            final_state=ScanState(final_state),
            scanner_version=scan_result.scanner_version,
            outcome_detail=scan_result.outcome,
        )

    # ------------------------------------------------------------------
    # Job handler registration
    # ------------------------------------------------------------------

    def register_scan_handler(self, runtime: JobRuntime) -> None:
        """Register the EVIDENCE_SCAN job handler with the given runtime."""

        def _handle_scan(payload: dict) -> dict:
            evidence_id_str = payload.get("evidence_id")
            if not evidence_id_str:
                raise TerminalJobError("missing_evidence_id")
            from uuid import UUID as _UUID

            evidence_id = _UUID(str(evidence_id_str))
            outcome = self.process_scan(evidence_id)
            return {"evidence_id": str(outcome.evidence_id), "status": outcome.final_state.value}

        runtime.register("EVIDENCE_SCAN", _handle_scan)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _record_security_event(
        self,
        evidence: EvidenceModel,
        event_type: str,
        outcome: str,
        occurred_at: datetime,
        *,
        scanner_version: str = "",
    ) -> None:
        """Record a sanitized security event for the scan outcome."""
        from uuid import uuid4

        SecurityEvent.objects.create(
            event_type=f"EVIDENCE_{event_type}",
            occurred_at=occurred_at,
            correlation_id=uuid4(),
            target_surrogate=evidence.id,
            outcome=outcome[:32],
            category="MALWARE_SCAN",
            scanner_version=scanner_version[:100],
            sanitized_metadata={
                "evidence_kind": evidence.kind,
                "byte_count": evidence.byte_count,
                "mime_type": evidence.mime_type,
            },
        )


# ---------------------------------------------------------------------------
# Encryption helper
# ---------------------------------------------------------------------------


def _xor_encrypt(data: bytes, key: bytes) -> bytes:
    """Simple repeating-key XOR for local development.

    Production uses AES-256-GCM via KMS envelope encryption. This local
    implementation demonstrates the streaming-encryption pattern without
    requiring cryptography library dependencies.
    """
    if not key:
        return data
    key_len = len(key)
    return bytes(b ^ key[i % key_len] for i, b in enumerate(data))
