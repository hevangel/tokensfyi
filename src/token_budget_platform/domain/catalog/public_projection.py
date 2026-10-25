"""Public projection builder — the sole private-to-public DTO boundary.

This module defines the PublicRecordDTO and PublicProjectionBuilder which form
the only path for private model data to reach public templates or JSON responses.

Requirements: 1.3, 4.2, 5.8, 6.5, 9.3, 9.5
- Only enumerated allowlisted fields appear in the public DTO.
- Contact data, capability material, evidence, internal team names,
  private notes, and unreviewed free text have no public DTO field.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

# --- Verification status definitions (Req 6.5) ---

VERIFICATION_STATUS_DEFINITIONS: dict[str, str] = {
    "UNVERIFIED": (
        "This record has not been supported by evidence. "
        "It reflects contributor-provided information without independent confirmation."
    ),
    "EVIDENCE_SUBMITTED": (
        "The contributor has supplied supporting evidence for this record. "
        "A moderator has not yet reviewed the evidence."
    ),
    "VERIFIED": ("A moderator has reviewed and confirmed the supporting evidence for this record."),
    "DISPUTED": (
        "A community report has raised a concern about this record. "
        "The record remains visible while the report is under review."
    ),
    "REJECTED": (
        "A moderator has reviewed and rejected the evidence or accuracy claim for this record."
    ),
}


# --- Public DTO (allowlisted fields only) ---


@dataclasses.dataclass(frozen=True, slots=True)
class PublicRecordDTO:
    """Immutable public projection of a token-budget record.

    This is the ONLY data structure that may be passed to public templates
    or JSON API serializers. Fields are an explicit allowlist; no private
    model attribute can be added without modifying this class.
    """

    # Identifiers
    id: UUID
    public_reference: UUID

    # Organization
    organization_name: str
    organization_slug: str
    organization_industry: str
    organization_size_band: str
    organization_region: str

    # Classification
    model_provider: str
    ai_product: str
    workload_category: str
    budget_scope: str
    geographic_region: str

    # Core budget data
    token_budget: Decimal
    input_tokens: Decimal | None
    output_tokens: Decimal | None

    # Reporting period
    reporting_start: date
    reporting_end: date
    reporting_timezone: str

    # Optional usage/cost
    token_usage: Decimal | None
    cost_minor_units: Decimal | None
    cost_currency: str | None
    cost_basis: str | None

    # Provenance and status
    verification_status: str
    verification_status_definition: str
    publication_date: datetime | None
    last_reviewed_date: datetime | None
    correction_date: date | None
    source_date: date
    methodology_notes: str
    contributor_attribution: str

    # Staleness indicator
    is_stale: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        result: dict[str, Any] = {}
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if isinstance(value, UUID):
                result[field.name] = str(value)
            elif isinstance(value, (date, datetime)):
                result[field.name] = value.isoformat()
            elif isinstance(value, Decimal):
                result[field.name] = str(value)
            else:
                result[field.name] = value
        return result


# --- Scope explanation (Req 1.1) ---

SCOPE_EXPLANATION = (
    "Token Budget Platform publishes benchmarks for organizational AI token budgets: "
    "approved quantities of model input or output tokens available to a person, team, "
    "workload, or organization during a defined reporting period, with optional actual "
    "usage and cost information. "
    "This product does not cover cryptocurrency holdings, compensation, "
    "authentication tokens, or model context-window specifications."
)


# --- Fixed browse headings (Req 1.4) ---

BROWSE_HEADINGS: list[str] = [
    "Organization",
    "Model Provider",
    "AI Product",
    "Workload Category",
    "Budget Scope",
    "Token Budget",
    "Reporting Period",
    "Geographic Region",
    "Verification Status",
]


# --- Public projection builder ---


class PublicProjectionBuilder:
    """The sole private-to-public boundary.

    Accepts a private model (or dict representation) and produces a
    PublicRecordDTO containing only allowlisted fields. Unknown or private
    fields are silently excluded — they have no corresponding DTO attribute.
    """

    # Fields that must NEVER appear in public output
    _FORBIDDEN_FIELDS: frozenset[str] = frozenset(
        {
            "contributor_contact",
            "encrypted_email",
            "email_blind_index",
            "wrapped_key_reference",
            "consent_purposes",
            "management_capabilities",
            "token_digest",
            "capability_digest",
            "session_digest",
            "evidence",
            "object_key",
            "encrypted_content",
            "scan_state",
            "scan_result",
            "scanner_version",
            "private_payload",
            "private_notes",
            "encrypted_explanation",
            "encrypted_rationale",
            "claimant_surrogate",
            "password_hash",
            "encrypted_mfa_secret",
            "recovery_code_hashes",
            "suppressed_at",
            "duplicate_flag",
        }
    )

    @classmethod
    def from_record(cls, record: Any, *, observation_date: date | None = None) -> PublicRecordDTO:
        """Build a PublicRecordDTO from a private TokenBudgetRecord model instance.

        The record must have related Organization, ControlledValue FK fields
        resolved (use select_related in queries).
        """
        from token_budget_platform.domain.catalog.staleness import is_stale

        obs = observation_date or date.today()

        verification_status = getattr(record, "verification_status", "UNVERIFIED")
        status_def = VERIFICATION_STATUS_DEFINITIONS.get(verification_status, "")

        return PublicRecordDTO(
            id=record.id,
            public_reference=record.public_reference,
            organization_name=record.organization.name,
            organization_slug=record.organization.slug,
            organization_industry=record.organization.industry,
            organization_size_band=record.organization.size_band.label,
            organization_region=record.organization.region.label,
            model_provider=record.model_provider.label,
            ai_product=record.ai_product.label,
            workload_category=record.workload_category.label,
            budget_scope=record.budget_scope,
            geographic_region=record.region.label,
            token_budget=record.token_budget,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            reporting_start=record.reporting_start,
            reporting_end=record.reporting_end,
            reporting_timezone=record.reporting_timezone,
            token_usage=record.token_usage,
            cost_minor_units=record.cost_minor_units,
            cost_currency=record.cost_currency,
            cost_basis=record.cost_basis,
            verification_status=verification_status,
            verification_status_definition=status_def,
            publication_date=record.published_at,
            last_reviewed_date=record.last_reviewed_at,
            correction_date=record.correction_date,
            source_date=record.source_date,
            methodology_notes=record.methodology_notes,
            contributor_attribution=record.contributor_attribution,
            is_stale=is_stale(record.reporting_end, obs),
        )

    @classmethod
    def validate_no_private_leakage(cls, data: dict[str, Any]) -> None:
        """Raise ValueError if any forbidden field appears in output data.

        Used as a defensive check in serializers and template contexts.
        """
        leaked = cls._FORBIDDEN_FIELDS & set(data.keys())
        if leaked:
            raise ValueError(f"Private fields leaked to public output: {sorted(leaked)}")
