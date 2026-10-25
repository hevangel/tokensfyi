"""Tests for controlled vocabularies, aliases, and organization merging.

Validates Requirements 12.1, 12.2, 12.3:
- 12.1: New values apply to subsequent submissions only; historical references valid.
- 12.2: Submitted org name matching alias resolves to canonical Organization_Profile.
- 12.3: Merging duplicate Organization_Profiles preserves every Token_Budget_Record
         and Audit_Record under the canonical profile.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from token_budget_platform.adapters.django.models import (
    AuditRecord,
    ControlledValue,
    ControlledValueKind,
    MethodologyVersion,
    ModerationCase,
    ModerationSource,
    Organization,
    OrganizationAlias,
    RecordVersion,
    TokenBudgetRecord,
)
from token_budget_platform.application.governance.services import (
    AliasConflictError,
    ControlledVocabularyService,
    GovernanceError,
    OrganizationMergeError,
    OrganizationMergeService,
    OrganizationNotFoundError,
)


@pytest.fixture
def vocab_service() -> ControlledVocabularyService:
    return ControlledVocabularyService()


@pytest.fixture
def merge_service() -> OrganizationMergeService:
    return OrganizationMergeService()


@pytest.fixture
def now():
    return timezone.now()


@pytest.fixture
def methodology_version():
    return MethodologyVersion.objects.create(
        version=1,
        name="Test Methodology v1",
        rules={},
        is_active=True,
        activated_at=timezone.now(),
    )


@pytest.fixture
def region_value(now):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.REGION,
        code="us-east",
        label="US East",
        normalized_label="us east",
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def size_band_value(now):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.SIZE_BAND,
        code="small",
        label="1-50 employees",
        normalized_label="1-50 employees",
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def provider_value(now):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.MODEL_PROVIDER,
        code="openai",
        label="OpenAI",
        normalized_label="openai",
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def product_value(now, provider_value):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.AI_PRODUCT,
        code="chatgpt-enterprise",
        label="ChatGPT Enterprise",
        normalized_label="chatgpt enterprise",
        parent=provider_value,
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def workload_value(now):
    return ControlledValue.objects.create(
        kind=ControlledValueKind.WORKLOAD_CATEGORY,
        code="software-dev",
        label="Software Development",
        normalized_label="software development",
        active_from=now - timedelta(days=30),
        vocabulary_version=1,
    )


@pytest.fixture
def organization_a(region_value, size_band_value):
    return Organization.objects.create(
        name="Acme Corp",
        normalized_name="acme corp",
        slug="acme-corp",
        industry="Technology",
        size_band=size_band_value,
        region=region_value,
    )


@pytest.fixture
def organization_b(region_value, size_band_value):
    return Organization.objects.create(
        name="Beta Inc",
        normalized_name="beta inc",
        slug="beta-inc",
        industry="Finance",
        size_band=size_band_value,
        region=region_value,
    )


@pytest.fixture
def record_for_org_a(
    organization_a,
    provider_value,
    product_value,
    workload_value,
    region_value,
    methodology_version,
    now,
):
    return TokenBudgetRecord.objects.create(
        organization=organization_a,
        budget_scope="TEAM",
        seat_count=10,
        model_provider=provider_value,
        ai_product=product_value,
        workload_category=workload_value,
        region=region_value,
        organization_search="acme corp",
        provider_search="openai",
        product_search="chatgpt enterprise",
        token_budget=Decimal("1000000"),
        reporting_start=now.date() - timedelta(days=30),
        reporting_end=now.date(),
        reporting_timezone="UTC",
        source_date=now.date(),
        methodology_version=methodology_version,
    )


# --- Requirement 12.1: Controlled Value Versioning ---


@pytest.mark.django_db
class TestControlledValueVersioning:
    """Req 12.1: new values apply to subsequent submissions only."""

    def test_active_values_returns_only_currently_active(self, vocab_service, now):
        """Only values active at query time are returned."""
        active = ControlledValue.objects.create(
            kind=ControlledValueKind.MODEL_PROVIDER,
            code="active-provider",
            label="Active Provider",
            normalized_label="active provider",
            active_from=now - timedelta(days=10),
            vocabulary_version=1,
        )
        ControlledValue.objects.create(
            kind=ControlledValueKind.MODEL_PROVIDER,
            code="expired-provider",
            label="Expired Provider",
            normalized_label="expired provider",
            active_from=now - timedelta(days=30),
            active_to=now - timedelta(days=1),
            vocabulary_version=1,
        )
        ControlledValue.objects.create(
            kind=ControlledValueKind.MODEL_PROVIDER,
            code="future-provider",
            label="Future Provider",
            normalized_label="future provider",
            active_from=now + timedelta(days=10),
            vocabulary_version=2,
        )

        result = vocab_service.active_values(ControlledValueKind.MODEL_PROVIDER, at=now)
        ids = [v.id for v in result]

        assert active.id in ids
        assert len(ids) == 1

    def test_active_for_new_draft_returns_all_active_kinds(self, vocab_service, now):
        """New drafts see only currently active values across all kinds."""
        ControlledValue.objects.create(
            kind=ControlledValueKind.MODEL_PROVIDER,
            code="prov1",
            label="Provider One",
            normalized_label="provider one",
            active_from=now - timedelta(days=5),
            vocabulary_version=1,
        )
        ControlledValue.objects.create(
            kind=ControlledValueKind.REGION,
            code="eu-west",
            label="EU West",
            normalized_label="eu west",
            active_from=now - timedelta(days=5),
            vocabulary_version=1,
        )

        snapshot = vocab_service.active_for_new_draft(at=now)

        assert len(snapshot["MODEL_PROVIDER"]) == 1
        assert len(snapshot["REGION"]) == 1
        assert snapshot["MODEL_PROVIDER"][0]["code"] == "prov1"

    def test_publish_value_deactivates_old_preserving_references(
        self, vocab_service, provider_value, now
    ):
        """Publishing a new version deactivates the old one but keeps the row."""
        new_value = vocab_service.publish_value(
            kind=ControlledValueKind.MODEL_PROVIDER,
            code="openai",
            label="OpenAI (Updated)",
            effective_at=now,
        )

        provider_value.refresh_from_db()
        assert provider_value.active_to == now
        assert new_value.active_to is None
        assert new_value.vocabulary_version > provider_value.vocabulary_version
        # Historical reference still exists in DB
        assert ControlledValue.objects.filter(id=provider_value.id).exists()

    def test_deactivated_value_excluded_from_new_drafts(self, vocab_service, provider_value, now):
        """After deactivation, value doesn't appear for new submissions."""
        vocab_service.deactivate_value(provider_value.id, effective_at=now)

        snapshot = vocab_service.active_for_new_draft(at=now + timedelta(seconds=1))
        provider_codes = [v["code"] for v in snapshot["MODEL_PROVIDER"]]

        assert "openai" not in provider_codes

    def test_historical_record_retains_reference_after_deactivation(
        self, vocab_service, provider_value, record_for_org_a, now
    ):
        """Existing records keep their FK to the deactivated value (no cascade)."""
        vocab_service.deactivate_value(provider_value.id, effective_at=now)
        record_for_org_a.refresh_from_db()

        assert record_for_org_a.model_provider_id == provider_value.id

    def test_publish_value_validates_kind(self, vocab_service):
        """Invalid controlled-value kind raises GovernanceError."""
        with pytest.raises(GovernanceError, match="unsupported"):
            vocab_service.publish_value(kind="INVALID_KIND", code="test", label="Test")

    def test_publish_value_requires_nonempty_code_and_label(self, vocab_service):
        """Empty code or label raises GovernanceError."""
        with pytest.raises(GovernanceError, match="non-empty"):
            vocab_service.publish_value(
                kind=ControlledValueKind.MODEL_PROVIDER, code="", label="Test"
            )
        with pytest.raises(GovernanceError, match="non-empty"):
            vocab_service.publish_value(
                kind=ControlledValueKind.MODEL_PROVIDER, code="test", label=""
            )

    def test_versions_for_new_draft_tracks_max_active_version(self, vocab_service, now):
        """The version snapshot reflects the highest active vocabulary version."""
        ControlledValue.objects.create(
            kind=ControlledValueKind.WORKLOAD_CATEGORY,
            code="cat-a",
            label="Category A",
            normalized_label="category a",
            active_from=now - timedelta(days=10),
            vocabulary_version=3,
        )
        ControlledValue.objects.create(
            kind=ControlledValueKind.WORKLOAD_CATEGORY,
            code="cat-b",
            label="Category B",
            normalized_label="category b",
            active_from=now - timedelta(days=5),
            vocabulary_version=5,
        )

        versions = vocab_service.versions_for_new_draft(at=now)

        assert versions["WORKLOAD_CATEGORY"] == 5


# --- Requirement 12.2: Alias Resolution ---


@pytest.mark.django_db
class TestAliasResolution:
    """Req 12.2: submitted org name matching alias resolves to canonical profile."""

    def test_resolve_by_normalized_name(self, vocab_service, organization_a):
        """Exact normalized name match resolves to the organization."""
        result = vocab_service.resolve("Acme Corp")
        assert result.id == organization_a.id

    def test_resolve_by_alias(self, vocab_service, organization_a, now):
        """A documented alias resolves to the canonical organization."""
        OrganizationAlias.objects.create(
            organization=organization_a,
            alias="ACME Corporation",
            normalized_alias="acme corporation",
            provenance="User submission",
            active_from=now - timedelta(days=5),
        )

        result = vocab_service.resolve("ACME Corporation")
        assert result.id == organization_a.id

    def test_resolve_case_insensitive(self, vocab_service, organization_a):
        """Resolution is case-insensitive via normalization."""
        result = vocab_service.resolve("ACME CORP")
        assert result.id == organization_a.id

    def test_resolve_alias_respects_active_dates(self, vocab_service, organization_a, now):
        """Expired aliases don't resolve."""
        OrganizationAlias.objects.create(
            organization=organization_a,
            alias="Old Name",
            normalized_alias="old name",
            provenance="Historical",
            active_from=now - timedelta(days=60),
            active_to=now - timedelta(days=1),
        )

        with pytest.raises(OrganizationNotFoundError):
            vocab_service.resolve("Old Name", at=now)

    def test_resolve_unknown_raises_not_found(self, vocab_service):
        """Unmatched name raises OrganizationNotFoundError."""
        with pytest.raises(OrganizationNotFoundError):
            vocab_service.resolve("Unknown Company")

    def test_add_alias_rejects_cross_org_conflict(
        self, vocab_service, organization_a, organization_b, now
    ):
        """Adding an alias that's active on another org raises AliasConflictError."""
        OrganizationAlias.objects.create(
            organization=organization_b,
            alias="shared alias",
            normalized_alias="shared alias",
            provenance="First usage",
            active_from=now - timedelta(days=10),
        )

        with pytest.raises(AliasConflictError):
            vocab_service.add_alias(
                organization=organization_a,
                alias="Shared Alias",
                provenance="Second usage",
                active_from=now,
            )

    def test_add_alias_normalizes_text(self, vocab_service, organization_a, now):
        """Alias normalization is case-folded and whitespace-collapsed."""
        alias = vocab_service.add_alias(
            organization=organization_a,
            alias="  Acme   Inc  ",
            provenance="Submission form",
            active_from=now,
        )

        assert alias.normalized_alias == "acme inc"

    def test_resolve_follows_merge_chain(
        self, vocab_service, merge_service, organization_a, organization_b, now
    ):
        """After merge, resolving the source org name returns the canonical org."""
        merge_service.merge(source=organization_b, canonical=organization_a)

        result = vocab_service.resolve("Beta Inc")
        assert result.id == organization_a.id


# --- Requirement 12.3: Organization Merge Preservation ---


@pytest.mark.django_db
class TestOrganizationMerge:
    """Req 12.3: merge preserves every Token_Budget_Record and Audit_Record."""

    def test_merge_reassigns_records_to_canonical(
        self,
        merge_service,
        organization_a,
        organization_b,
        record_for_org_a,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_version,
        now,
    ):
        """All Token_Budget_Records move to the canonical organization."""
        record_b = TokenBudgetRecord.objects.create(
            organization=organization_b,
            budget_scope="INDIVIDUAL",
            seat_count=1,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="beta inc",
            provider_search="openai",
            product_search="chatgpt enterprise",
            token_budget=Decimal("500000"),
            reporting_start=now.date() - timedelta(days=30),
            reporting_end=now.date(),
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_version,
        )

        result = merge_service.merge(source=organization_b, canonical=organization_a)

        record_b.refresh_from_db()
        assert record_b.organization_id == organization_a.id
        assert result.records_reassigned == 1
        assert result.canonical_id == organization_a.id
        assert result.source_id == organization_b.id

    def test_merge_preserves_record_ids(
        self,
        merge_service,
        organization_a,
        organization_b,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_version,
        now,
    ):
        """Record primary keys do not change during merge."""
        record = TokenBudgetRecord.objects.create(
            organization=organization_b,
            budget_scope="TEAM",
            seat_count=5,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="beta inc",
            provider_search="openai",
            product_search="chatgpt enterprise",
            token_budget=Decimal("2000000"),
            reporting_start=now.date() - timedelta(days=30),
            reporting_end=now.date(),
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_version,
        )
        original_id = record.id

        merge_service.merge(source=organization_b, canonical=organization_a)
        record.refresh_from_db()

        assert record.id == original_id

    def test_merge_preserves_record_versions(
        self,
        merge_service,
        organization_a,
        organization_b,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_version,
        now,
    ):
        """RecordVersion history is preserved (still linked through record FK)."""
        record = TokenBudgetRecord.objects.create(
            organization=organization_b,
            budget_scope="INDIVIDUAL",
            seat_count=1,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="beta inc",
            provider_search="openai",
            product_search="chatgpt enterprise",
            token_budget=Decimal("100000"),
            reporting_start=now.date() - timedelta(days=30),
            reporting_end=now.date(),
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_version,
        )
        version = RecordVersion.objects.create(
            record=record,
            version=1,
            public_payload={"token_budget": "100000"},
            private_payload={},
            changed_fields=[],
            change_reason="Initial submission",
            actor_type="CONTRIBUTOR",
        )

        merge_service.merge(source=organization_b, canonical=organization_a)
        version.refresh_from_db()

        assert version.record_id == record.id  # unchanged

    def test_merge_preserves_moderation_cases(
        self,
        merge_service,
        organization_a,
        organization_b,
        provider_value,
        product_value,
        workload_value,
        region_value,
        methodology_version,
        now,
    ):
        """ModerationCases remain linked to their records (not reassigned)."""
        record = TokenBudgetRecord.objects.create(
            organization=organization_b,
            budget_scope="TEAM",
            seat_count=5,
            model_provider=provider_value,
            ai_product=product_value,
            workload_category=workload_value,
            region=region_value,
            organization_search="beta inc",
            provider_search="openai",
            product_search="chatgpt enterprise",
            token_budget=Decimal("500000"),
            reporting_start=now.date() - timedelta(days=30),
            reporting_end=now.date(),
            reporting_timezone="UTC",
            source_date=now.date(),
            methodology_version=methodology_version,
        )
        case = ModerationCase.objects.create(
            record=record,
            source=ModerationSource.SUBMISSION,
            category="review",
            state="OPEN",
            priority=50,
            opened_at=now,
        )

        merge_service.merge(source=organization_b, canonical=organization_a)
        case.refresh_from_db()

        # Case stays linked to the record, record is now under canonical org
        assert case.record_id == record.id
        record.refresh_from_db()
        assert record.organization_id == organization_a.id

    def test_merge_preserves_audit_targets(
        self, merge_service, organization_a, organization_b, now
    ):
        """AuditRecords referencing the source org retain their target_id unchanged."""
        import uuid

        audit = AuditRecord.objects.create(
            sequence=1,
            actor_surrogate=uuid.uuid4(),
            action="organization.update",
            target_type="Organization",
            target_id=organization_b.id,
            occurred_at=now,
            outcome="SUCCESS",
            correlation_id=uuid.uuid4(),
        )

        merge_service.merge(source=organization_b, canonical=organization_a)
        audit.refresh_from_db()

        # Audit target_id is NOT changed — preserves history
        assert audit.target_id == organization_b.id

    def test_merge_redirects_aliases(self, merge_service, organization_a, organization_b, now):
        """Source org aliases are redirected to the canonical org."""
        alias = OrganizationAlias.objects.create(
            organization=organization_b,
            alias="Beta Corporation",
            normalized_alias="beta corporation",
            provenance="Historical",
            active_from=now - timedelta(days=30),
        )

        result = merge_service.merge(source=organization_b, canonical=organization_a)
        alias.refresh_from_db()

        assert alias.organization_id == organization_a.id
        assert result.aliases_redirected == 1

    def test_merge_deactivates_conflicting_aliases(
        self, merge_service, organization_a, organization_b, now
    ):
        """If canonical already has the same alias, source alias is deactivated."""
        OrganizationAlias.objects.create(
            organization=organization_a,
            alias="shared",
            normalized_alias="shared",
            provenance="Canonical",
            active_from=now - timedelta(days=30),
        )
        source_alias = OrganizationAlias.objects.create(
            organization=organization_b,
            alias="Shared",
            normalized_alias="shared",
            provenance="Source",
            active_from=now - timedelta(days=20),
        )

        merge_service.merge(source=organization_b, canonical=organization_a)
        source_alias.refresh_from_db()

        assert source_alias.active_to is not None

    def test_merge_marks_source_inactive(self, merge_service, organization_a, organization_b):
        """Source organization is marked inactive and points to canonical."""
        merge_service.merge(source=organization_b, canonical=organization_a)
        organization_b.refresh_from_db()

        assert organization_b.is_active is False
        assert organization_b.merge_target_id == organization_a.id

    def test_merge_same_org_raises_error(self, merge_service, organization_a):
        """Cannot merge an organization into itself."""
        with pytest.raises(OrganizationMergeError):
            merge_service.merge(source=organization_a, canonical=organization_a)

    def test_merge_into_inactive_org_raises_error(
        self, merge_service, organization_a, organization_b
    ):
        """Cannot merge into an inactive canonical organization."""
        organization_a.is_active = False
        organization_a.save()

        with pytest.raises(OrganizationMergeError, match="active"):
            merge_service.merge(source=organization_b, canonical=organization_a)

    def test_merge_already_redirected_raises_error(
        self, merge_service, organization_a, organization_b, region_value, size_band_value
    ):
        """Cannot merge an already-redirected source."""
        org_c = Organization.objects.create(
            name="Charlie LLC",
            normalized_name="charlie llc",
            slug="charlie-llc",
            industry="Healthcare",
            size_band=size_band_value,
            region=region_value,
        )
        # First merge B → A
        merge_service.merge(source=organization_b, canonical=organization_a)

        # Attempting to merge B → C fails because B is already redirected
        with pytest.raises(OrganizationMergeError, match="already redirected"):
            merge_service.merge(source=organization_b, canonical=org_c)


# --- Normalization Tests ---


@pytest.mark.django_db
class TestNormalization:
    """ControlledVocabularyService.normalize behavior."""

    def test_normalize_casefolds(self, vocab_service):
        assert vocab_service.normalize("OpenAI") == "openai"

    def test_normalize_collapses_whitespace(self, vocab_service):
        assert vocab_service.normalize("  Hello   World  ") == "hello world"

    def test_normalize_nfkc(self, vocab_service):
        # ﬁ (U+FB01) should normalize to "fi"
        assert vocab_service.normalize("\ufb01le") == "file"

    def test_normalize_rejects_non_string(self, vocab_service):
        with pytest.raises(GovernanceError, match="text"):
            vocab_service.normalize(123)
