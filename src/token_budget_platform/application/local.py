"""Deterministic local-development configuration and baseline data."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid5

from django.db import transaction

from token_budget_platform.adapters.django.models import (
    BudgetScope,
    ControlledValue,
    ControlledValueKind,
    MethodologyVersion,
    Organization,
    PublicationState,
    TokenBudgetRecord,
    VerificationStatus,
)

LOCAL_REQUIRED_SETTINGS = (
    "SECRET_KEY",
    "DATABASE_URL",
    "ALLOWED_HOSTS",
    "MINIO_ENDPOINT",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "MAILPIT_HOST",
    "CLAMAV_HOST",
)
BASELINE_SOURCE_PATH = "fixtures/baseline/records.json"
BASELINE_DATE = date(2026, 1, 1)
BASELINE_DATETIME = datetime(2026, 1, 1, tzinfo=UTC)
BASELINE_NAMESPACE = UUID("3b5a1c59-3a3f-4e3c-9b70-4f3d2e1c0a99")


def baseline_id(kind: str, value: str) -> UUID:
    """Return an identifier stable across reset and developer machines."""
    return uuid5(BASELINE_NAMESPACE, f"{kind}:{value}")


def configuration_diagnostics(
    environ: dict[str, str], required: tuple[str, ...] = LOCAL_REQUIRED_SETTINGS
) -> list[str]:
    """Return all missing local settings without echoing secret values."""
    return [name for name in required if not environ.get(name, "").strip()]


def _value(
    kind: str, code: str, label: str, *, parent: ControlledValue | None = None
) -> ControlledValue:
    return ControlledValue.objects.update_or_create(
        id=baseline_id("controlled-value", f"{kind}:{code}"),
        defaults={
            "kind": kind,
            "code": code,
            "label": label,
            "normalized_label": label.casefold(),
            "parent": parent,
            "active_from": BASELINE_DATETIME,
            "active_to": None,
            "vocabulary_version": 1,
        },
    )[0]


def _organization(
    name: str, industry: str, size_band: ControlledValue, region: ControlledValue
) -> Organization:
    return Organization.objects.update_or_create(
        id=baseline_id("organization", name),
        defaults={
            "name": name,
            "normalized_name": name.casefold(),
            "slug": name.casefold().replace(" ", "-"),
            "industry": industry,
            "size_band": size_band,
            "region": region,
            "merge_target": None,
            "is_active": True,
        },
    )[0]


def seed_baseline() -> dict[str, Any]:
    """Create the checked-in 20-record local baseline idempotently."""
    with transaction.atomic():
        size = _value(ControlledValueKind.SIZE_BAND, "SCALEUP", "201-500 employees")
        region = _value(ControlledValueKind.REGION, "NORTH_AMERICA", "North America")
        providers = [
            _value(ControlledValueKind.MODEL_PROVIDER, "OPENAI", "OpenAI"),
            _value(ControlledValueKind.MODEL_PROVIDER, "ANTHROPIC", "Anthropic"),
        ]
        products = [
            _value(ControlledValueKind.AI_PRODUCT, "CODING_ASSISTANT", "Coding assistant"),
            _value(ControlledValueKind.AI_PRODUCT, "SUPPORT_COPILOT", "Support copilot"),
        ]
        workloads = [
            _value(
                ControlledValueKind.WORKLOAD_CATEGORY,
                "SOFTWARE_DEVELOPMENT",
                "Software development",
            ),
            _value(ControlledValueKind.WORKLOAD_CATEGORY, "CUSTOMER_SUPPORT", "Customer support"),
            _value(ControlledValueKind.WORKLOAD_CATEGORY, "RESEARCH", "Research"),
        ]
        organizations = [
            _organization("Acme Analytics", "Technology", size, region),
            _organization("Northstar Health", "Healthcare", size, region),
            _organization("Pioneer Retail", "Retail", size, region),
        ]
        methodology = MethodologyVersion.objects.update_or_create(
            id=baseline_id("methodology", "1"),
            defaults={
                "version": 1,
                "name": "Local baseline normalization v1",
                "rules": {"period_days": "inclusive", "baseline": True},
                "is_active": True,
                "activated_at": BASELINE_DATETIME,
            },
        )[0]
        statuses = list(VerificationStatus.values)
        records: list[TokenBudgetRecord] = []
        for index in range(20):
            organization = organizations[index % len(organizations)]
            workload = workloads[index % len(workloads)]
            provider = providers[index % len(providers)]
            product = products[index % len(products)]
            status = statuses[index % len(statuses)]
            budget_scope = BudgetScope.WORKLOAD if index % 3 == 2 else BudgetScope.TEAM
            budget = 1_000_000 + (index * 125_000)
            record = TokenBudgetRecord.objects.update_or_create(
                id=baseline_id("record", str(index + 1)),
                defaults={
                    "organization": organization,
                    "budget_scope": budget_scope,
                    "seat_count": None if budget_scope == BudgetScope.WORKLOAD else 10 + index,
                    "workload_count": 100 + index if budget_scope == BudgetScope.WORKLOAD else None,
                    "model_provider": provider,
                    "ai_product": product,
                    "workload_category": workload,
                    "region": region,
                    "organization_search": organization.name.casefold(),
                    "provider_search": provider.label.casefold(),
                    "product_search": product.label.casefold(),
                    "token_budget": budget,
                    "input_tokens": budget * 2 // 3,
                    "output_tokens": budget - (budget * 2 // 3),
                    "reporting_start": BASELINE_DATE,
                    "reporting_end": date(2026, 1, 30),
                    "reporting_timezone": "UTC",
                    "source_date": BASELINE_DATE,
                    "token_usage": budget // 2,
                    "cost_minor_units": 10000 + index * 100,
                    "cost_currency": "USD",
                    "cost_basis": "monthly local baseline",
                    "methodology_notes": (
                        f"Deterministic local baseline record {index + 1}; "
                        f"source path: {BASELINE_SOURCE_PATH}"
                    ),
                    "contributor_attribution": "Anonymous Contributor",
                    "publication_state": (
                        PublicationState.REJECTED
                        if status == VerificationStatus.REJECTED
                        else PublicationState.APPROVED
                    ),
                    "verification_status": status,
                    "duplicate_flag": False,
                    "active_version": 1,
                    "methodology_version": methodology,
                    "published_at": BASELINE_DATETIME,
                    "last_reviewed_at": BASELINE_DATETIME,
                    "correction_date": None,
                    "withdrawn_at": None,
                    "suppressed_at": None,
                },
            )[0]
            records.append(record)

    return {
        "records": len(records),
        "organizations": len(organizations),
        "workload_categories": len(workloads),
        "verification_statuses": sorted({record.verification_status for record in records}),
        "source_path": BASELINE_SOURCE_PATH,
    }
