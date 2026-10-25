"""Value objects and data transfer types for the submission domain boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SubmissionPayload:
    """Normalized submission data ready for draft persistence or preview.

    All text values are Unicode NFC normalized.  Controlled-value IDs are
    validated strings referencing active values at draft-creation time.
    """

    # Organization
    organization_name: str
    industry: str
    size_band_id: str
    region_id: str

    # Product
    model_provider_id: str
    ai_product_id: str
    workload_category_id: str
    budget_scope: str

    # Budget
    token_budget: Decimal
    input_tokens: Decimal | None = None
    output_tokens: Decimal | None = None
    seat_count: int | None = None
    workload_count: int | None = None

    # Period
    reporting_start: date = field(default_factory=date.today)
    reporting_end: date = field(default_factory=date.today)
    reporting_timezone: str = "UTC"
    source_date: date = field(default_factory=date.today)

    # Optional usage
    token_usage: Decimal | None = None

    # Optional cost (all or none)
    cost_minor_units: Decimal | None = None
    cost_currency: str | None = None
    cost_basis: str | None = None

    # Attribution
    contributor_attribution: str = "ANONYMOUS"
    methodology_notes: str = ""

    # Optional evidence
    evidence_url: str | None = None

    # Optional contact
    contact_email: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary suitable for JSON storage."""
        result: dict[str, Any] = {
            "organization_name": self.organization_name,
            "industry": self.industry,
            "size_band_id": self.size_band_id,
            "region_id": self.region_id,
            "model_provider_id": self.model_provider_id,
            "ai_product_id": self.ai_product_id,
            "workload_category_id": self.workload_category_id,
            "budget_scope": self.budget_scope,
            "token_budget": str(self.token_budget),
            "reporting_start": self.reporting_start.isoformat(),
            "reporting_end": self.reporting_end.isoformat(),
            "reporting_timezone": self.reporting_timezone,
            "source_date": self.source_date.isoformat(),
            "contributor_attribution": self.contributor_attribution,
            "methodology_notes": self.methodology_notes,
        }
        if self.input_tokens is not None:
            result["input_tokens"] = str(self.input_tokens)
        if self.output_tokens is not None:
            result["output_tokens"] = str(self.output_tokens)
        if self.seat_count is not None:
            result["seat_count"] = self.seat_count
        if self.workload_count is not None:
            result["workload_count"] = self.workload_count
        if self.token_usage is not None:
            result["token_usage"] = str(self.token_usage)
        if self.cost_minor_units is not None:
            result["cost_minor_units"] = str(self.cost_minor_units)
        if self.cost_currency is not None:
            result["cost_currency"] = self.cost_currency
        if self.cost_basis is not None:
            result["cost_basis"] = self.cost_basis
        if self.evidence_url is not None:
            result["evidence_url"] = self.evidence_url
        if self.contact_email is not None:
            result["contact_email"] = self.contact_email
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubmissionPayload:
        """Deserialize from a stored dictionary."""
        return cls(
            organization_name=data["organization_name"],
            industry=data["industry"],
            size_band_id=data["size_band_id"],
            region_id=data["region_id"],
            model_provider_id=data["model_provider_id"],
            ai_product_id=data["ai_product_id"],
            workload_category_id=data["workload_category_id"],
            budget_scope=data["budget_scope"],
            token_budget=Decimal(data["token_budget"]),
            input_tokens=Decimal(data["input_tokens"]) if data.get("input_tokens") else None,
            output_tokens=Decimal(data["output_tokens"]) if data.get("output_tokens") else None,
            seat_count=data.get("seat_count"),
            workload_count=data.get("workload_count"),
            reporting_start=date.fromisoformat(data["reporting_start"]),
            reporting_end=date.fromisoformat(data["reporting_end"]),
            reporting_timezone=data.get("reporting_timezone", "UTC"),
            source_date=date.fromisoformat(data["source_date"]),
            token_usage=Decimal(data["token_usage"]) if data.get("token_usage") else None,
            cost_minor_units=(
                Decimal(data["cost_minor_units"]) if data.get("cost_minor_units") else None
            ),
            cost_currency=data.get("cost_currency"),
            cost_basis=data.get("cost_basis"),
            contributor_attribution=data.get("contributor_attribution", "ANONYMOUS"),
            methodology_notes=data.get("methodology_notes", ""),
            evidence_url=data.get("evidence_url"),
            contact_email=data.get("contact_email"),
        )


@dataclass(frozen=True, slots=True)
class DraftResult:
    """Result of creating or updating a submission draft."""

    draft_id: UUID
    capability_token: str
    expires_at: datetime
    validation_result: dict[str, Any]
    vocabulary_versions: dict[str, int]
