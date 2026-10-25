"""Persistence models for the token-budget platform.

The ORM is an adapter boundary: domain/application code should depend on repository
interfaces rather than importing these models.  Private fields are deliberately
kept separate from the public projection fields on :class:`TokenBudgetRecord`.
"""

from __future__ import annotations

import secrets
import time
import uuid
from typing import Any

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


def uuid7() -> uuid.UUID:
    """Return a sortable UUIDv7 without requiring Python 3.14's ``uuid7`` API."""
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    value = (timestamp_ms << 80) | (0x7 << 76) | secrets.randbits(76)
    value &= ~(0b11 << 62)
    value |= 0b10 << 62
    return uuid.UUID(int=value)


class TimestampedModel(models.Model):
    """Shared identity, UTC timestamps, and optimistic concurrency version."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    row_version = models.PositiveBigIntegerField(default=1)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.row_version += 1
        super().save(*args, **kwargs)


class ControlledValueKind(models.TextChoices):
    MODEL_PROVIDER = "MODEL_PROVIDER", "Model provider"
    AI_PRODUCT = "AI_PRODUCT", "AI product"
    WORKLOAD_CATEGORY = "WORKLOAD_CATEGORY", "Workload category"
    SIZE_BAND = "SIZE_BAND", "Organization size band"
    REGION = "REGION", "Geographic region"


class BudgetScope(models.TextChoices):
    INDIVIDUAL = "INDIVIDUAL", "Individual"
    TEAM = "TEAM", "Team"
    WORKLOAD = "WORKLOAD", "Workload"
    ORGANIZATION = "ORGANIZATION", "Organization"


class VerificationStatus(models.TextChoices):
    UNVERIFIED = "UNVERIFIED", "Unverified"
    EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED", "Evidence submitted"
    VERIFIED = "VERIFIED", "Verified"
    DISPUTED = "DISPUTED", "Disputed"
    REJECTED = "REJECTED", "Rejected"


class PublicationState(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    QUEUED = "QUEUED", "Queued"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    CHANGES_REQUESTED = "CHANGES_REQUESTED", "Changes requested"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"


class Organization(TimestampedModel):
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True)
    industry = models.CharField(max_length=200)
    size_band = models.ForeignKey(
        "ControlledValue", on_delete=models.PROTECT, related_name="organizations_by_size"
    )
    region = models.ForeignKey(
        "ControlledValue", on_delete=models.PROTECT, related_name="organizations_by_region"
    )
    merge_target = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="merged_sources"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "slug"]),
            models.Index(fields=["normalized_name"]),
        ]
        constraints = [models.CheckConstraint(condition=~Q(name=""), name="org_name_nonempty")]


class ControlledValue(TimestampedModel):
    kind = models.CharField(max_length=32, choices=ControlledValueKind.choices)
    code = models.CharField(max_length=100)
    label = models.CharField(max_length=200)
    normalized_label = models.CharField(max_length=200)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    active_from = models.DateTimeField()
    active_to = models.DateTimeField(null=True, blank=True)
    vocabulary_version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["kind", "label", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "code", "vocabulary_version"],
                name="controlled_kind_code_version_uniq",
            ),
            models.CheckConstraint(
                condition=Q(active_to__isnull=True) | Q(active_to__gt=models.F("active_from")),
                name="controlled_active_range",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "active_from", "active_to"]),
            models.Index(fields=["kind", "normalized_label"]),
        ]


class OrganizationAlias(TimestampedModel):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="aliases")
    alias = models.CharField(max_length=200)
    normalized_alias = models.CharField(max_length=200)
    provenance = models.CharField(max_length=400)
    active_from = models.DateTimeField()
    active_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_alias", "organization"], name="org_alias_org_uniq"
            ),
            models.CheckConstraint(
                condition=Q(active_to__isnull=True) | Q(active_to__gt=models.F("active_from")),
                name="org_alias_active_range",
            ),
        ]
        indexes = [models.Index(fields=["normalized_alias"])]


class MethodologyVersion(TimestampedModel):
    version = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    rules = models.JSONField(default=dict)
    is_active = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)


class TokenBudgetRecord(TimestampedModel):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="records")
    budget_scope = models.CharField(max_length=20, choices=BudgetScope.choices)
    seat_count = models.PositiveBigIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(10**9)]
    )
    workload_count = models.PositiveBigIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(10**9)]
    )
    model_provider = models.ForeignKey(
        "ControlledValue", on_delete=models.PROTECT, related_name="provider_records"
    )
    ai_product = models.ForeignKey(
        "ControlledValue", on_delete=models.PROTECT, related_name="product_records"
    )
    workload_category = models.ForeignKey(
        "ControlledValue", on_delete=models.PROTECT, related_name="workload_records"
    )
    region = models.ForeignKey(
        "ControlledValue", on_delete=models.PROTECT, related_name="region_records"
    )
    organization_search = models.CharField(max_length=200, db_index=True)
    provider_search = models.CharField(max_length=200, db_index=True)
    product_search = models.CharField(max_length=200, db_index=True)
    token_budget = models.DecimalField(
        max_digits=30, decimal_places=0, validators=[MinValueValidator(1)]
    )
    input_tokens = models.DecimalField(
        max_digits=30, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    output_tokens = models.DecimalField(
        max_digits=30, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    reporting_start = models.DateField()
    reporting_end = models.DateField()
    reporting_timezone = models.CharField(max_length=64, default="UTC")
    source_date = models.DateField()
    token_usage = models.DecimalField(
        max_digits=30, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    cost_minor_units = models.DecimalField(
        max_digits=18, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    cost_currency = models.CharField(max_length=3, null=True, blank=True)
    cost_basis = models.CharField(max_length=200, null=True, blank=True)
    methodology_notes = models.TextField(max_length=4000, blank=True)
    contributor_attribution = models.CharField(max_length=32, default="ANONYMOUS")
    publication_state = models.CharField(
        max_length=24, choices=PublicationState.choices, default=PublicationState.DRAFT
    )
    verification_status = models.CharField(
        max_length=24, choices=VerificationStatus.choices, default=VerificationStatus.UNVERIFIED
    )
    duplicate_flag = models.BooleanField(default=False)
    duplicate_fingerprint = models.CharField(max_length=128, blank=True, db_index=True)
    public_reference = models.UUIDField(default=uuid7, unique=True, editable=False)
    active_version = models.PositiveIntegerField(default=1)
    methodology_version = models.ForeignKey(
        MethodologyVersion, on_delete=models.PROTECT, related_name="records"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    correction_date = models.DateField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    suppressed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["publication_state", "suppressed_at", "withdrawn_at", "id"],
                name="record_public_eligibility_idx",
            ),
            models.Index(fields=["organization", "publication_state", "reporting_end", "id"]),
            models.Index(fields=["model_provider", "ai_product", "workload_category", "region"]),
            models.Index(fields=["token_budget", "id"]),
            models.Index(fields=["reporting_end", "id"]),
            models.Index(fields=["verification_status", "id"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(reporting_start__lte=models.F("reporting_end")),
                name="record_period_order",
            ),
            models.CheckConstraint(
                condition=Q(input_tokens__isnull=True, output_tokens__isnull=True)
                | (
                    Q(input_tokens__isnull=False)
                    & Q(output_tokens__isnull=False)
                    & models.Q(input_tokens__gte=0)
                    & models.Q(output_tokens__gte=0)
                ),
                name="record_allocation_pair",
            ),
            models.CheckConstraint(
                condition=Q(seat_count__isnull=True) | Q(workload_count__isnull=True),
                name="record_one_denominator",
            ),
            models.CheckConstraint(
                condition=Q(budget_scope="WORKLOAD", workload_count__isnull=False)
                | ~Q(budget_scope="WORKLOAD")
                | Q(workload_count__isnull=True),
                name="record_workload_count_coherence",
            ),
            models.CheckConstraint(
                condition=Q(
                    cost_minor_units__isnull=True,
                    cost_currency__isnull=True,
                    cost_basis__isnull=True,
                )
                | (
                    Q(cost_minor_units__isnull=False)
                    & Q(cost_currency__isnull=False)
                    & Q(cost_basis__isnull=False)
                ),
                name="record_cost_coherence",
            ),
        ]

    @classmethod
    def public_queryset(cls):
        return cls.objects.filter(
            publication_state=PublicationState.APPROVED,
            withdrawn_at__isnull=True,
            suppressed_at__isnull=True,
        )


class RecordVersion(TimestampedModel):
    record = models.ForeignKey(TokenBudgetRecord, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    public_payload = models.JSONField(default=dict)
    private_payload = models.JSONField(default=dict)
    changed_fields = models.JSONField(default=list)
    change_reason = models.CharField(max_length=4000)
    actor_type = models.CharField(max_length=32)
    actor_surrogate = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["record", "version"], name="record_version_number_uniq")
        ]
        indexes = [models.Index(fields=["record", "version"])]


class NormalizedMetric(TimestampedModel):
    record = models.ForeignKey(
        TokenBudgetRecord, on_delete=models.PROTECT, related_name="normalized_metrics"
    )
    metric_type = models.CharField(max_length=64)
    value = models.DecimalField(max_digits=40, decimal_places=18)
    display_scale = models.PositiveSmallIntegerField(default=2)
    formula_code = models.CharField(max_length=100)
    methodology_version = models.ForeignKey(
        MethodologyVersion, on_delete=models.PROTECT, related_name="metrics"
    )
    source_values = models.JSONField(default=dict)
    calculated_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["record", "metric_type", "methodology_version"],
                name="metric_record_type_method_uniq",
            )
        ]
        indexes = [models.Index(fields=["metric_type", "value", "id"])]


class AggregateSnapshot(TimestampedModel):
    dimension_key = models.CharField(max_length=200)
    dimension_values = models.JSONField(default=dict)
    count = models.PositiveIntegerField()
    minimum = models.DecimalField(max_digits=40, decimal_places=18)
    maximum = models.DecimalField(max_digits=40, decimal_places=18)
    median = models.DecimalField(max_digits=40, decimal_places=18)
    source_watermark = models.DateTimeField()
    methodology_version = models.ForeignKey(
        MethodologyVersion, on_delete=models.PROTECT, related_name="aggregates"
    )
    source_record_ids = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(count__gte=3), name="aggregate_minimum_group_size")
        ]
        indexes = [models.Index(fields=["dimension_key", "methodology_version", "id"])]


class SubmissionDraft(TimestampedModel):
    payload = models.JSONField(default=dict)
    capability_digest = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    vocabulary_versions = models.JSONField(default=dict)
    validation_version = models.PositiveIntegerField(default=1)
    validation_result = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]


class ManagementCapability(TimestampedModel):
    record = models.ForeignKey(
        TokenBudgetRecord, on_delete=models.PROTECT, related_name="management_capabilities"
    )
    key_version = models.PositiveIntegerField()
    token_digest = models.CharField(max_length=128, unique=True)
    scope = models.CharField(max_length=64, default="MANAGE_SUBMISSION")
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_at__gt=models.F("issued_at")),
                name="capability_expiry_after_issue",
            )
        ]
        indexes = [models.Index(fields=["record", "revoked_at", "expires_at"])]


class ManagementSession(TimestampedModel):
    record = models.ForeignKey(
        TokenBudgetRecord, on_delete=models.PROTECT, related_name="management_sessions"
    )
    session_digest = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)


class ContributorContact(TimestampedModel):
    record = models.OneToOneField(
        TokenBudgetRecord, on_delete=models.PROTECT, related_name="contributor_contact"
    )
    encrypted_email = models.BinaryField()
    email_blind_index = models.CharField(max_length=128)
    wrapped_key_reference = models.UUIDField()
    consent_purposes = models.JSONField(default=list)
    retention_until = models.DateTimeField(null=True, blank=True)
    erased_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["email_blind_index"])]


class EvidenceKind(models.TextChoices):
    FILE = "FILE", "File"
    PRIVATE_LINK = "PRIVATE_LINK", "Private link"
    EXPLANATION = "EXPLANATION", "Explanation"


class EvidenceScanState(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CLEAN = "CLEAN", "Clean"
    QUARANTINED = "QUARANTINED", "Quarantined"
    SCAN_ERROR = "SCAN_ERROR", "Scan error"


class Evidence(TimestampedModel):
    record = models.ForeignKey(TokenBudgetRecord, on_delete=models.PROTECT, related_name="evidence")
    kind = models.CharField(max_length=20, choices=EvidenceKind.choices)
    object_key = models.CharField(max_length=1024, null=True, blank=True)
    encrypted_content = models.BinaryField(null=True, blank=True)
    filename = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=127, blank=True)
    byte_count = models.PositiveBigIntegerField(default=0)
    wrapped_key_reference = models.UUIDField(null=True, blank=True)
    scan_state = models.CharField(
        max_length=20, choices=EvidenceScanState.choices, default=EvidenceScanState.PENDING
    )
    scan_result = models.CharField(max_length=400, blank=True)
    scanner_version = models.CharField(max_length=100, blank=True)
    submitted_at = models.DateTimeField()
    scanned_at = models.DateTimeField(null=True, blank=True)
    final_resolution_at = models.DateTimeField(null=True, blank=True)
    delete_due_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    legal_hold = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(byte_count__lte=10 * 1024 * 1024), name="evidence_max_size"
            ),
            models.CheckConstraint(
                condition=Q(kind="FILE", object_key__isnull=False) | ~Q(kind="FILE"),
                name="evidence_file_object_key",
            ),
        ]
        indexes = [
            models.Index(fields=["scan_state", "record"]),
            models.Index(fields=["delete_due_at", "legal_hold"]),
        ]


class ModerationSource(models.TextChoices):
    SUBMISSION = "SUBMISSION", "Submission"
    REPORT = "REPORT", "Community report"
    DISPUTE = "DISPUTE", "Dispute"
    DUPLICATE = "DUPLICATE", "Duplicate"
    VERIFICATION = "VERIFICATION", "Verification"


class ModerationCaseState(models.TextChoices):
    OPEN = "OPEN", "Open"
    RESOLVED = "RESOLVED", "Resolved"
    DISMISSED = "DISMISSED", "Dismissed"


class ModerationCase(TimestampedModel):
    record = models.ForeignKey(
        TokenBudgetRecord, on_delete=models.PROTECT, related_name="moderation_cases"
    )
    source = models.CharField(max_length=20, choices=ModerationSource.choices)
    category = models.CharField(max_length=100)
    encrypted_explanation = models.BinaryField(null=True, blank=True)
    private_notes = models.BinaryField(null=True, blank=True)
    state = models.CharField(
        max_length=20, choices=ModerationCaseState.choices, default=ModerationCaseState.OPEN
    )
    priority = models.PositiveSmallIntegerField(default=50)
    previous_verification_status = models.CharField(
        max_length=24, choices=VerificationStatus.choices, null=True, blank=True
    )
    claimant_surrogate = models.UUIDField(null=True, blank=True)
    assigned_staff = models.ForeignKey(
        "StaffUser", null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_cases"
    )
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "priority", "opened_at"]),
            models.Index(fields=["record", "source", "state"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["record", "category"],
                condition=Q(state="OPEN", source="DISPUTE"),
                name="one_open_dispute_category",
            )
        ]


class ModerationDecision(TimestampedModel):
    case = models.ForeignKey(ModerationCase, on_delete=models.PROTECT, related_name="decisions")
    action = models.CharField(max_length=64)
    reason_code = models.CharField(max_length=100)
    encrypted_rationale = models.BinaryField(null=True, blank=True)
    staff = models.ForeignKey(
        "StaffUser", on_delete=models.PROTECT, related_name="moderation_decisions"
    )
    resulting_publication_state = models.CharField(max_length=24, choices=PublicationState.choices)
    resulting_verification_status = models.CharField(
        max_length=24, choices=VerificationStatus.choices
    )
    decided_at = models.DateTimeField()


class CorrectionEvent(TimestampedModel):
    record = models.ForeignKey(
        TokenBudgetRecord, on_delete=models.PROTECT, related_name="corrections"
    )
    version = models.ForeignKey(
        RecordVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="corrections"
    )
    public_correction_date = models.DateField()
    material_value_changed = models.BooleanField(default=False)
    private_reason = models.BinaryField(null=True, blank=True)
    actor_surrogate = models.UUIDField(null=True, blank=True)


class StaffRole(models.TextChoices):
    MODERATOR = "MODERATOR", "Moderator"
    ADMINISTRATOR = "ADMINISTRATOR", "Administrator"


class StaffUser(TimestampedModel):
    username = models.CharField(max_length=150, unique=True)
    password_hash = models.CharField(max_length=256)
    role = models.CharField(max_length=20, choices=StaffRole.choices)
    is_active = models.BooleanField(default=True)
    encrypted_mfa_secret = models.BinaryField()
    recovery_code_hashes = models.JSONField(default=list)
    failed_attempt_window_start = models.DateTimeField(null=True, blank=True)
    failed_attempt_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    session_revocation_epoch = models.PositiveBigIntegerField(default=0)


class AuditRecord(TimestampedModel):
    sequence = models.PositiveBigIntegerField(unique=True)
    actor_surrogate = models.UUIDField(null=True, blank=True)
    actor_role = models.CharField(max_length=20, choices=StaffRole.choices, null=True, blank=True)
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.UUIDField(null=True, blank=True)
    occurred_at = models.DateTimeField()
    outcome = models.CharField(max_length=32)
    correlation_id = models.UUIDField()
    source_network_pseudonym = models.CharField(max_length=128, blank=True)
    sanitized_metadata = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=["occurred_at", "id"]),
            models.Index(fields=["target_type", "target_id"]),
        ]


class SecurityEvent(TimestampedModel):
    event_type = models.CharField(max_length=100)
    occurred_at = models.DateTimeField()
    correlation_id = models.UUIDField()
    actor_or_network_pseudonym = models.CharField(max_length=128, blank=True)
    target_surrogate = models.UUIDField(null=True, blank=True)
    outcome = models.CharField(max_length=32)
    category = models.CharField(max_length=100)
    scanner_version = models.CharField(max_length=100, blank=True)
    sanitized_metadata = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=["occurred_at", "event_type", "id"])]


class JobState(models.TextChoices):
    PENDING = "PENDING", "Pending"
    RUNNING = "RUNNING", "Running"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"
    DEAD = "DEAD", "Dead"


class Job(TimestampedModel):
    job_type = models.CharField(max_length=100)
    payload_version = models.PositiveIntegerField(default=1)
    payload = models.JSONField(default=dict)
    state = models.CharField(max_length=20, choices=JobState.choices, default=JobState.PENDING)
    priority = models.PositiveSmallIntegerField(default=50)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    available_at = models.DateTimeField()
    leased_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    worker_id = models.CharField(max_length=128, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    last_error_category = models.CharField(max_length=100, blank=True)
    terminal_outcome = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "priority", "available_at"]),
            models.Index(fields=["lease_expires_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(attempts__lte=models.F("max_attempts")), name="job_attempts_bounded"
            )
        ]


class OutboxEvent(TimestampedModel):
    aggregate_type = models.CharField(max_length=100)
    aggregate_id = models.UUIDField()
    event_type = models.CharField(max_length=100)
    event_version = models.PositiveIntegerField(default=1)
    payload = models.JSONField(default=dict)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["published_at", "created_at"]),
            models.Index(fields=["aggregate_type", "aggregate_id"]),
        ]


class IdempotencyRecord(TimestampedModel):
    requester_scope = models.CharField(max_length=128)
    key_digest = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=128)
    response_status = models.PositiveSmallIntegerField()
    response_body_reference = models.CharField(max_length=255)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["requester_scope", "key_digest"], name="idempotency_scope_key_uniq"
            )
        ]
        indexes = [models.Index(fields=["expires_at"])]


class OperationalGateType(models.TextChoices):
    BACKUP_MUTATIONS = "BACKUP_MUTATIONS", "Backup mutations"


class OperationalGate(TimestampedModel):
    environment = models.CharField(max_length=64)
    gate_type = models.CharField(max_length=32, choices=OperationalGateType.choices)
    is_open = models.BooleanField(default=True)
    signed_source_event_reference = models.CharField(max_length=255)
    reason_code = models.CharField(max_length=100)
    observed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "gate_type"], name="operational_gate_env_type_uniq"
            )
        ]
        indexes = [models.Index(fields=["environment", "gate_type", "is_open"])]
