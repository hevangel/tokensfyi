"""Build the public preview of a submission payload before confirmation.

Req 5.8: the preview is generated through the same public projection builder
used for published records, so contributors confirm exactly the fields that
would be published — nothing more.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from token_budget_platform.domain.catalog.public_projection import (
    SCOPE_EXPLANATION,
    VERIFICATION_STATUS_DEFINITIONS,
    PublicRecordDTO,
)
from token_budget_platform.domain.catalog.staleness import is_stale


def build_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a submission payload into the public preview shape.

    Accepts raw payload values (ISO strings, decimal strings, controlled-value
    labels) and returns the same field set as PublicRecordDTO.to_dict plus the
    scope explanation and status definition, with a neutral "preview" marker
    in place of identifiers that do not exist until confirmation.
    """
    today = date.today()
    reporting_start = _as_date(payload.get("reporting_start"))
    reporting_end = _as_date(payload.get("reporting_end"))
    token_budget = _as_decimal(payload.get("token_budget"))

    budget_scope = str(payload.get("budget_scope", ""))
    verification_status = "UNVERIFIED"

    dto = PublicRecordDTO(
        id=_PREVIEW_ID,
        public_reference=_PREVIEW_ID,
        organization_name=str(payload.get("organization_name", "")),
        organization_slug="",
        organization_industry=str(payload.get("industry", "")),
        organization_size_band=str(payload.get("size_band_label", payload.get("size_band", ""))),
        organization_region=str(payload.get("region_label", payload.get("region", ""))),
        model_provider=str(payload.get("model_provider_label", payload.get("model_provider", ""))),
        ai_product=str(payload.get("ai_product_label", payload.get("ai_product", ""))),
        workload_category=str(
            payload.get("workload_category_label", payload.get("workload_category", ""))
        ),
        budget_scope=budget_scope,
        geographic_region=str(payload.get("region_label", payload.get("region", ""))),
        token_budget=token_budget or Decimal(0),
        input_tokens=_as_decimal(payload.get("input_tokens")),
        output_tokens=_as_decimal(payload.get("output_tokens")),
        reporting_start=reporting_start or today,
        reporting_end=reporting_end or today,
        reporting_timezone=str(payload.get("reporting_timezone", "UTC")),
        token_usage=_as_decimal(payload.get("token_usage")),
        cost_minor_units=_as_decimal(payload.get("cost_minor_units")),
        cost_currency=_optional(payload.get("cost_currency")),
        cost_basis=_optional(payload.get("cost_basis")),
        verification_status=verification_status,
        verification_status_definition=VERIFICATION_STATUS_DEFINITIONS[verification_status],
        publication_date=None,
        last_reviewed_date=None,
        correction_date=None,
        source_date=_as_date(payload.get("source_date")) or today,
        methodology_notes=str(payload.get("methodology_notes", "")),
        contributor_attribution=str(payload.get("contributor_attribution", "ANONYMOUS")),
        is_stale=bool(reporting_end and is_stale(reporting_end, today)),
    )

    result = dto.to_dict()
    result["is_preview"] = True
    result["scope_explanation"] = SCOPE_EXPLANATION
    return result


def dto_to_preview(dto: PublicRecordDTO) -> dict[str, Any]:
    """Convert a projected DTO back into the preview payload shape."""
    result = dataclasses.asdict(dto)
    result = _jsonify(result)
    result["is_preview"] = False
    result["scope_explanation"] = SCOPE_EXPLANATION
    return result


_PREVIEW_ID = UUID(int=0)  # neutral nil identifier; no record exists before confirmation


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _jsonify(value: Any) -> Any:
    from datetime import date as _date
    from datetime import datetime as _datetime
    from decimal import Decimal as _Decimal
    from uuid import UUID as _UUID

    if isinstance(value, _UUID):
        return str(value)
    if isinstance(value, (_date, _datetime)):
        return value.isoformat()
    if isinstance(value, _Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    return value
