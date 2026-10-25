"""Versioned duplicate detection for token-budget submissions.

Canonicalizes organization, scope, product, token budget, and period into
a versioned fingerprint string and computes a digest.  A match sets the
duplicate review flag; it never automatically rejects a submission.

The fingerprint version allows future changes to the canonicalization
algorithm without invalidating historical fingerprints.  When the version
changes, existing records can be re-fingerprinted by a background job.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Current fingerprint algorithm version.  Bump when canonicalization rules change.
FINGERPRINT_VERSION = 1


@dataclass(frozen=True, slots=True)
class DuplicateFingerprint:
    """Immutable fingerprint computed from submission fields."""

    version: int
    digest: str

    @property
    def canonical_key(self) -> str:
        """Return the versioned key suitable for storage and comparison."""
        return f"v{self.version}:{self.digest}"


def _normalize_text(value: str) -> str:
    """Normalize text for fingerprinting: lowercase, strip, collapse whitespace."""
    import unicodedata

    text = unicodedata.normalize("NFC", value)
    return " ".join(text.lower().strip().split())


def compute_fingerprint(
    *,
    organization_name: str,
    budget_scope: str,
    ai_product_id: str,
    token_budget: Decimal | int | str,
    reporting_start: date,
    reporting_end: date,
) -> DuplicateFingerprint:
    """Compute a versioned duplicate fingerprint from canonical submission fields.

    The fingerprint canonicalizes:
    - Organization name (case-insensitive, whitespace-collapsed, NFC-normalized)
    - Budget scope (uppercased)
    - AI product controlled-value ID (exact string)
    - Token budget (integer string representation)
    - Reporting period (ISO date pair)

    Two submissions with the same fingerprint are potential duplicates and
    should be flagged for moderator review without automatic rejection.
    """
    # Canonicalize each component
    canonical_org = _normalize_text(organization_name)
    canonical_scope = budget_scope.upper().strip()
    canonical_product = str(ai_product_id).strip()
    canonical_budget = str(int(Decimal(str(token_budget))))
    canonical_period = f"{reporting_start.isoformat()}|{reporting_end.isoformat()}"

    # Build the fingerprint input string with field separators
    fingerprint_input = "\x1f".join(
        [
            f"v{FINGERPRINT_VERSION}",
            canonical_org,
            canonical_scope,
            canonical_product,
            canonical_budget,
            canonical_period,
        ]
    )

    # Compute SHA-256 digest
    digest = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()

    return DuplicateFingerprint(version=FINGERPRINT_VERSION, digest=digest)


def check_duplicate(
    *,
    organization_name: str,
    budget_scope: str,
    ai_product_id: str,
    token_budget: Decimal | int | str,
    reporting_start: date,
    reporting_end: date,
    existing_fingerprints: set[str],
) -> tuple[bool, DuplicateFingerprint]:
    """Check whether a submission matches any existing fingerprint.

    Returns a tuple of (is_duplicate, fingerprint).  The caller is responsible
    for flagging the submission for moderator review when ``is_duplicate`` is True.
    This function never causes automatic rejection.
    """
    fingerprint = compute_fingerprint(
        organization_name=organization_name,
        budget_scope=budget_scope,
        ai_product_id=ai_product_id,
        token_budget=token_budget,
        reporting_start=reporting_start,
        reporting_end=reporting_end,
    )

    is_duplicate = fingerprint.canonical_key in existing_fingerprints
    return is_duplicate, fingerprint
