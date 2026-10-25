"""Deterministic baseline seed for local development.

Creates at least 20 Token_Budget_Records spanning:
- 3 organizations
- 3 workload categories
- Every verification status (UNVERIFIED, EVIDENCE_SUBMITTED, VERIFIED, DISPUTED, REJECTED)

All IDs and timestamps are deterministic so the baseline is reproducible
and reset returns the database to an identical state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from token_budget_platform.adapters.django.models import (
    BudgetScope,
    ControlledValue,
    ControlledValueKind,
    MethodologyVersion,
    Organization,
    PublicationState,
    RecordVersion,
    StaffRole,
    StaffUser,
    TokenBudgetRecord,
    VerificationStatus,
)


def _uuid(seed: int) -> uuid.UUID:
    """Create a deterministic UUID from a seed integer."""
    return uuid.UUID(int=seed, version=4)


# Fixed reference timestamp for all seed data
SEED_TIME = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)


class Command(BaseCommand):
    help = (
        "Load deterministic baseline seed data"
        " (at least 20 records, 3 orgs, 3 categories, all statuses)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Verify seed data is present without creating it.",
        )

    def handle(self, *args, **options) -> None:
        if options["check"]:
            count = TokenBudgetRecord.objects.count()
            if count >= 20:
                self.stdout.write(self.style.SUCCESS(f"  Seed OK: {count} records present."))
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Seed incomplete: only {count} records. Run `manage.py seed`."
                    )
                )
            return

        with transaction.atomic():
            self._create_seed_data()

        count = TokenBudgetRecord.objects.count()
        self.stdout.write(self.style.SUCCESS(f"  Seed complete: {count} records created."))

    def _create_seed_data(self) -> None:
        # Methodology version
        methodology = MethodologyVersion.objects.create(
            id=_uuid(1000),
            version=1,
            name="Initial methodology v1",
            rules={
                "budget_30d": "token_budget * 30 / days",
                "per_seat_30d": "token_budget * 30 / (seat_count * days)",
            },
            is_active=True,
            activated_at=SEED_TIME,
        )

        # Controlled values: providers
        providers = _create_controlled_values(
            ControlledValueKind.MODEL_PROVIDER,
            [("OPENAI", "OpenAI"), ("ANTHROPIC", "Anthropic"), ("GOOGLE", "Google")],
            base_id=2000,
        )

        # Controlled values: products
        products = _create_controlled_values(
            ControlledValueKind.AI_PRODUCT,
            [
                ("GPT4O", "GPT-4o"),
                ("CLAUDE_SONNET", "Claude 3.5 Sonnet"),
                ("GEMINI_PRO", "Gemini 1.5 Pro"),
            ],
            base_id=3000,
        )

        # Controlled values: workload categories (at least 3)
        categories = _create_controlled_values(
            ControlledValueKind.WORKLOAD_CATEGORY,
            [
                ("SOFTWARE_DEV", "Software Development"),
                ("CUSTOMER_SUPPORT", "Customer Support"),
                ("RESEARCH", "Research"),
                ("CONTENT_GEN", "Content Generation"),
            ],
            base_id=4000,
        )

        # Controlled values: size bands
        size_bands = _create_controlled_values(
            ControlledValueKind.SIZE_BAND,
            [("SMALL", "1-50"), ("MEDIUM", "51-500"), ("LARGE", "501-5000")],
            base_id=5000,
        )

        # Controlled values: regions
        regions = _create_controlled_values(
            ControlledValueKind.REGION,
            [("NA", "North America"), ("EU", "Europe"), ("APAC", "Asia Pacific")],
            base_id=6000,
        )

        # Organizations (3)
        orgs = [
            Organization.objects.create(
                id=_uuid(7001),
                name="Acme Corp",
                normalized_name="acme corp",
                slug="acme-corp",
                industry="Technology",
                size_band=size_bands[2],  # Large
                region=regions[0],  # NA
                is_active=True,
            ),
            Organization.objects.create(
                id=_uuid(7002),
                name="Globex Industries",
                normalized_name="globex industries",
                slug="globex-industries",
                industry="Manufacturing",
                size_band=size_bands[1],  # Medium
                region=regions[1],  # EU
                is_active=True,
            ),
            Organization.objects.create(
                id=_uuid(7003),
                name="Initech Solutions",
                normalized_name="initech solutions",
                slug="initech-solutions",
                industry="Financial Services",
                size_band=size_bands[0],  # Small
                region=regions[2],  # APAC
                is_active=True,
            ),
        ]

        # Staff users for audit trails
        StaffUser.objects.create(
            id=_uuid(8001),
            username="mod_seed",
            password_hash="!unusable-seed-only",
            role=StaffRole.MODERATOR,
            is_active=True,
            encrypted_mfa_secret=b"seed-placeholder",
        )
        StaffUser.objects.create(
            id=_uuid(8002),
            username="admin_seed",
            password_hash="!unusable-seed-only",
            role=StaffRole.ADMINISTRATOR,
            is_active=True,
            encrypted_mfa_secret=b"seed-placeholder",
        )

        # Record definitions: 24 records ensuring coverage of all verification statuses,
        # 3 orgs, and 3+ workload categories
        records_spec = [
            # Acme Corp records (8)
            _rec(
                1,
                orgs[0],
                BudgetScope.TEAM,
                providers[0],
                products[0],
                categories[0],
                regions[0],
                500_000,
                VerificationStatus.UNVERIFIED,
                PublicationState.APPROVED,
                seat_count=25,
            ),
            _rec(
                2,
                orgs[0],
                BudgetScope.INDIVIDUAL,
                providers[0],
                products[0],
                categories[0],
                regions[0],
                50_000,
                VerificationStatus.EVIDENCE_SUBMITTED,
                PublicationState.APPROVED,
                seat_count=1,
            ),
            _rec(
                3,
                orgs[0],
                BudgetScope.ORGANIZATION,
                providers[1],
                products[1],
                categories[1],
                regions[0],
                2_000_000,
                VerificationStatus.VERIFIED,
                PublicationState.APPROVED,
                seat_count=200,
            ),
            _rec(
                4,
                orgs[0],
                BudgetScope.WORKLOAD,
                providers[2],
                products[2],
                categories[2],
                regions[0],
                100_000,
                VerificationStatus.DISPUTED,
                PublicationState.APPROVED,
                workload_count=1000,
            ),
            _rec(
                5,
                orgs[0],
                BudgetScope.TEAM,
                providers[0],
                products[0],
                categories[0],
                regions[0],
                750_000,
                VerificationStatus.REJECTED,
                PublicationState.APPROVED,
                seat_count=50,
            ),
            _rec(
                6,
                orgs[0],
                BudgetScope.INDIVIDUAL,
                providers[1],
                products[1],
                categories[2],
                regions[0],
                30_000,
                VerificationStatus.UNVERIFIED,
                PublicationState.QUEUED,
                seat_count=1,
            ),
            _rec(
                7,
                orgs[0],
                BudgetScope.TEAM,
                providers[2],
                products[2],
                categories[1],
                regions[0],
                1_500_000,
                VerificationStatus.VERIFIED,
                PublicationState.APPROVED,
                seat_count=100,
            ),
            _rec(
                8,
                orgs[0],
                BudgetScope.ORGANIZATION,
                providers[0],
                products[0],
                categories[0],
                regions[0],
                10_000_000,
                VerificationStatus.EVIDENCE_SUBMITTED,
                PublicationState.APPROVED,
                seat_count=500,
            ),
            # Globex Industries records (8)
            _rec(
                9,
                orgs[1],
                BudgetScope.TEAM,
                providers[1],
                products[1],
                categories[1],
                regions[1],
                800_000,
                VerificationStatus.VERIFIED,
                PublicationState.APPROVED,
                seat_count=40,
            ),
            _rec(
                10,
                orgs[1],
                BudgetScope.INDIVIDUAL,
                providers[0],
                products[0],
                categories[0],
                regions[1],
                60_000,
                VerificationStatus.UNVERIFIED,
                PublicationState.APPROVED,
                seat_count=1,
            ),
            _rec(
                11,
                orgs[1],
                BudgetScope.WORKLOAD,
                providers[2],
                products[2],
                categories[2],
                regions[1],
                200_000,
                VerificationStatus.EVIDENCE_SUBMITTED,
                PublicationState.APPROVED,
                workload_count=500,
            ),
            _rec(
                12,
                orgs[1],
                BudgetScope.ORGANIZATION,
                providers[1],
                products[1],
                categories[1],
                regions[1],
                5_000_000,
                VerificationStatus.DISPUTED,
                PublicationState.APPROVED,
                seat_count=300,
            ),
            _rec(
                13,
                orgs[1],
                BudgetScope.TEAM,
                providers[0],
                products[0],
                categories[2],
                regions[1],
                350_000,
                VerificationStatus.REJECTED,
                PublicationState.APPROVED,
                seat_count=20,
            ),
            _rec(
                14,
                orgs[1],
                BudgetScope.INDIVIDUAL,
                providers[2],
                products[2],
                categories[0],
                regions[1],
                45_000,
                VerificationStatus.VERIFIED,
                PublicationState.APPROVED,
                seat_count=1,
            ),
            _rec(
                15,
                orgs[1],
                BudgetScope.WORKLOAD,
                providers[1],
                products[1],
                categories[1],
                regions[1],
                900_000,
                VerificationStatus.UNVERIFIED,
                PublicationState.QUEUED,
                workload_count=2000,
            ),
            _rec(
                16,
                orgs[1],
                BudgetScope.TEAM,
                providers[0],
                products[0],
                categories[2],
                regions[1],
                1_200_000,
                VerificationStatus.EVIDENCE_SUBMITTED,
                PublicationState.APPROVED,
                seat_count=60,
            ),
            # Initech Solutions records (8)
            _rec(
                17,
                orgs[2],
                BudgetScope.INDIVIDUAL,
                providers[0],
                products[0],
                categories[0],
                regions[2],
                25_000,
                VerificationStatus.UNVERIFIED,
                PublicationState.APPROVED,
                seat_count=1,
            ),
            _rec(
                18,
                orgs[2],
                BudgetScope.TEAM,
                providers[1],
                products[1],
                categories[1],
                regions[2],
                400_000,
                VerificationStatus.VERIFIED,
                PublicationState.APPROVED,
                seat_count=15,
            ),
            _rec(
                19,
                orgs[2],
                BudgetScope.WORKLOAD,
                providers[2],
                products[2],
                categories[2],
                regions[2],
                150_000,
                VerificationStatus.DISPUTED,
                PublicationState.APPROVED,
                workload_count=750,
            ),
            _rec(
                20,
                orgs[2],
                BudgetScope.ORGANIZATION,
                providers[0],
                products[0],
                categories[0],
                regions[2],
                3_000_000,
                VerificationStatus.EVIDENCE_SUBMITTED,
                PublicationState.APPROVED,
                seat_count=45,
            ),
            _rec(
                21,
                orgs[2],
                BudgetScope.INDIVIDUAL,
                providers[1],
                products[1],
                categories[2],
                regions[2],
                35_000,
                VerificationStatus.REJECTED,
                PublicationState.APPROVED,
                seat_count=1,
            ),
            _rec(
                22,
                orgs[2],
                BudgetScope.TEAM,
                providers[2],
                products[2],
                categories[1],
                regions[2],
                600_000,
                VerificationStatus.VERIFIED,
                PublicationState.APPROVED,
                seat_count=30,
            ),
            _rec(
                23,
                orgs[2],
                BudgetScope.WORKLOAD,
                providers[0],
                products[0],
                categories[0],
                regions[2],
                80_000,
                VerificationStatus.UNVERIFIED,
                PublicationState.QUEUED,
                workload_count=300,
            ),
            _rec(
                24,
                orgs[2],
                BudgetScope.TEAM,
                providers[1],
                products[1],
                categories[2],
                regions[2],
                950_000,
                VerificationStatus.EVIDENCE_SUBMITTED,
                PublicationState.APPROVED,
                seat_count=35,
            ),
        ]

        for spec in records_spec:
            record = TokenBudgetRecord.objects.create(
                id=spec["id"],
                organization=spec["organization"],
                budget_scope=spec["budget_scope"],
                seat_count=spec.get("seat_count"),
                workload_count=spec.get("workload_count"),
                model_provider=spec["model_provider"],
                ai_product=spec["ai_product"],
                workload_category=spec["workload_category"],
                region=spec["region"],
                organization_search=spec["organization"].normalized_name,
                provider_search=spec["model_provider"].normalized_label,
                product_search=spec["ai_product"].normalized_label,
                token_budget=spec["token_budget"],
                reporting_start=spec["reporting_start"],
                reporting_end=spec["reporting_end"],
                reporting_timezone="UTC",
                source_date=spec["source_date"],
                methodology_notes=f"Seed record #{spec['number']} for local development.",
                contributor_attribution="ANONYMOUS",
                publication_state=spec["publication_state"],
                verification_status=spec["verification_status"],
                methodology_version=methodology,
                published_at=SEED_TIME
                if spec["publication_state"] == PublicationState.APPROVED
                else None,
                last_reviewed_at=SEED_TIME
                if spec["publication_state"] == PublicationState.APPROVED
                else None,
            )
            # Create initial record version
            RecordVersion.objects.create(
                id=_uuid(20_000 + spec["number"]),
                record=record,
                version=1,
                public_payload={"token_budget": str(spec["token_budget"])},
                private_payload={},
                changed_fields=[],
                change_reason="Initial seed submission",
                actor_type="SYSTEM",
            )


def _create_controlled_values(
    kind: str, items: list[tuple[str, str]], base_id: int
) -> list[ControlledValue]:
    """Create controlled-value records with deterministic IDs."""
    results = []
    for i, (code, label) in enumerate(items):
        cv = ControlledValue.objects.create(
            id=_uuid(base_id + i),
            kind=kind,
            code=code,
            label=label,
            normalized_label=label.lower(),
            active_from=SEED_TIME,
            vocabulary_version=1,
        )
        results.append(cv)
    return results


def _rec(
    number: int,
    org: Organization,
    scope: str,
    provider: ControlledValue,
    product: ControlledValue,
    category: ControlledValue,
    region: ControlledValue,
    budget: int,
    verification: str,
    publication: str,
    seat_count: int | None = None,
    workload_count: int | None = None,
) -> dict:
    """Build a record spec dictionary."""
    # Deterministic dates spread across 2024
    month = ((number - 1) % 12) + 1
    return {
        "number": number,
        "id": _uuid(10_000 + number),
        "organization": org,
        "budget_scope": scope,
        "seat_count": seat_count,
        "workload_count": workload_count,
        "model_provider": provider,
        "ai_product": product,
        "workload_category": category,
        "region": region,
        "token_budget": Decimal(budget),
        "reporting_start": date(2024, month, 1),
        "reporting_end": date(2024, month, 28),
        "source_date": date(2024, month, 15),
        "verification_status": verification,
        "publication_state": publication,
    }
