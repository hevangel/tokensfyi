"""Evidence lifecycle domain boundary.

Defines validation rules, type detection, and state-transition policies for
private evidence items associated with token-budget records.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024  # 10 MiB

# Declared MIME types accepted for file evidence
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "text/plain",
        "text/csv",
    }
)

# File extensions that correspond to the allowlist
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".txt",
        ".csv",
    }
)

# Magic byte signatures for binary detection
_PDF_MAGIC = b"%PDF"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


class EvidenceKind(str, Enum):
    FILE = "FILE"
    PRIVATE_LINK = "PRIVATE_LINK"
    EXPLANATION = "EXPLANATION"


class ScanState(str, Enum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    QUARANTINED = "QUARANTINED"
    SCAN_ERROR = "SCAN_ERROR"


class VerificationTransition(str, Enum):
    """Allowed verification-status transitions driven by moderator decisions."""

    VERIFY = "VERIFY"  # Evidence_Submitted → Verified
    REJECT = "REJECT"  # Evidence_Submitted → Rejected


# Map of (current_status, transition) → resulting status
ALLOWED_VERIFICATION_TRANSITIONS: dict[tuple[str, VerificationTransition], str] = {
    ("EVIDENCE_SUBMITTED", VerificationTransition.VERIFY): "VERIFIED",
    ("EVIDENCE_SUBMITTED", VerificationTransition.REJECT): "REJECTED",
}


class EvidenceRejectionReason(str, Enum):
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    DISALLOWED_EXTENSION = "DISALLOWED_EXTENSION"
    DISALLOWED_MIME_TYPE = "DISALLOWED_MIME_TYPE"
    CONTENT_TYPE_MISMATCH = "CONTENT_TYPE_MISMATCH"
    EMPTY_FILE = "EMPTY_FILE"


@dataclass(frozen=True, slots=True)
class EvidenceRejection:
    reason: EvidenceRejectionReason
    message: str


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_file_size(byte_count: int) -> EvidenceRejection | None:
    """Return a rejection if byte_count exceeds the 10 MiB limit."""
    if byte_count <= 0:
        return EvidenceRejection(
            reason=EvidenceRejectionReason.EMPTY_FILE,
            message="Evidence files must not be empty.",
        )
    if byte_count > MAX_EVIDENCE_BYTES:
        return EvidenceRejection(
            reason=EvidenceRejectionReason.FILE_TOO_LARGE,
            message=f"Evidence files must not exceed 10 MiB (received {byte_count} bytes).",
        )
    return None


def validate_extension(filename: str) -> EvidenceRejection | None:
    """Return a rejection if the filename extension is not in the allowlist."""
    dot_index = filename.rfind(".")
    if dot_index < 0:
        return EvidenceRejection(
            reason=EvidenceRejectionReason.DISALLOWED_EXTENSION,
            message=(
                f"File extension is required. "
                f"Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            ),
        )
    ext = filename[dot_index:].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return EvidenceRejection(
            reason=EvidenceRejectionReason.DISALLOWED_EXTENSION,
            message=(
                f"File extension '{ext}' is not allowed. "
                f"Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            ),
        )
    return None


def validate_declared_mime(declared_type: str) -> EvidenceRejection | None:
    """Return a rejection if the declared MIME type is not in the allowlist."""
    normalized = declared_type.strip().lower().split(";")[0].strip()
    if normalized not in ALLOWED_MIME_TYPES:
        return EvidenceRejection(
            reason=EvidenceRejectionReason.DISALLOWED_MIME_TYPE,
            message=(
                f"MIME type '{normalized}' is not allowed. "
                f"Allowed types: {', '.join(sorted(ALLOWED_MIME_TYPES))}."
            ),
        )
    return None


def detect_content_type(header: bytes) -> str | None:
    """Detect the actual content type from file magic bytes.

    Returns a MIME type string or None if detection is inconclusive.
    """
    if len(header) < 4:
        return None
    if header[:4] == _PDF_MAGIC:
        return "application/pdf"
    if header[:8] == _PNG_MAGIC:
        return "image/png"
    if header[:3] == _JPEG_MAGIC:
        return "image/jpeg"
    # For text/csv and text/plain, we check for valid UTF-8 text content
    try:
        sample = header[:512].decode("utf-8")
        # If it decodes cleanly and has no null bytes, treat as text
        if "\x00" not in sample:
            return "text/plain"  # text/csv also decodes as text
    except (UnicodeDecodeError, ValueError):
        pass
    return None


def validate_content_type_match(
    declared_type: str, detected_type: str | None
) -> EvidenceRejection | None:
    """Return a rejection if declared and detected types are incompatible."""
    if detected_type is None:
        # Cannot confirm; allow through (declared type already validated)
        return None
    normalized_declared = declared_type.strip().lower().split(";")[0].strip()
    # text/csv and text/plain both detect as text/plain, which is acceptable
    if normalized_declared == "text/csv" and detected_type == "text/plain":
        return None
    if normalized_declared == detected_type:
        return None
    return EvidenceRejection(
        reason=EvidenceRejectionReason.CONTENT_TYPE_MISMATCH,
        message=(
            f"Declared type '{normalized_declared}' does not match detected type '{detected_type}'."
        ),
    )


def validate_file_evidence(
    filename: str, declared_mime: str, byte_count: int, header: bytes
) -> EvidenceRejection | None:
    """Run all file-evidence validation checks in order; return first failure."""
    checks = [
        validate_file_size(byte_count),
        validate_extension(filename),
        validate_declared_mime(declared_mime),
        validate_content_type_match(declared_mime, detect_content_type(header)),
    ]
    for check in checks:
        if check is not None:
            return check
    return None
