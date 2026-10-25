"""Transactional outbox and at-least-once, idempotent job runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone as django_timezone

from token_budget_platform.adapters.django.models import Job, JobState, OutboxEvent
from token_budget_platform.domain.ports import CacheInvalidationPort, Clock, TelemetryPort


class JobPayloadError(ValueError):
    """A job payload attempted to carry data rather than identifiers."""


class JobConflictError(ValueError):
    """An idempotency key was reused for a different job."""


class RetryableJobError(RuntimeError):
    def __init__(self, category: str = "transient") -> None:
        super().__init__(category)
        self.category = category[:100]


class TerminalJobError(RuntimeError):
    def __init__(self, category: str = "terminal") -> None:
        super().__init__(category)
        self.category = category[:100]


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: UUID
    job_type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    idempotency_key: str


JobHandler = Callable[[dict[str, Any]], Mapping[str, Any] | None]


_PRIVATE_KEYS = (
    "email",
    "contact",
    "evidence",
    "body",
    "content",
    "secret",
    "token",
    "password",
    "text",
    "note",
)
_ALLOWED_SCALAR_KEYS = {
    "job_type",
    "event_type",
    "aggregate_type",
    "resource_type",
    "kind",
    "scope",
    "status",
    "version",
    "cache_key",
    "reason_code",
}


def validate_identifier_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy the small identifier-only JSON envelope used by jobs/events."""
    if not isinstance(payload, Mapping):
        raise JobPayloadError("job payload must be an object")
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            raise JobPayloadError("job payload keys must be non-empty strings")
        is_identifier = key.endswith("_id") or key.endswith("_ids")
        if not is_identifier and any(part in key.lower() for part in _PRIVATE_KEYS):
            raise JobPayloadError("job payload contains a private field")
        if not (is_identifier or key in _ALLOWED_SCALAR_KEYS):
            raise JobPayloadError(f"job payload field is not an identifier: {key}")
        result[key] = _validate_value(value)
    return result


def _validate_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str) and len(value) <= 256:
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 100:
        return [_validate_value(item) for item in value]
    raise JobPayloadError("job payload values must be bounded identifiers")


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


class TransactionalOutbox:
    """Writes an outbox event in the caller's transaction with no external side effects."""

    def enqueue(
        self,
        *,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        event_version: int = 1,
    ) -> OutboxEvent:
        safe_payload = validate_identifier_payload(payload or {})
        with transaction.atomic():
            return OutboxEvent.objects.create(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                event_version=event_version,
                payload=safe_payload,
            )

    def dispatch(self, *, limit: int = 100, clock: Clock | None = None) -> int:
        """Translate pending outbox rows to uniquely keyed jobs atomically."""
        if limit < 1:
            return 0
        now = (clock or _SystemClock()).now()
        dispatched = 0
        while dispatched < limit:
            with transaction.atomic():
                event = (
                    OutboxEvent.objects.select_for_update(skip_locked=True)
                    .filter(published_at__isnull=True)
                    .order_by("created_at", "id")
                    .first()
                )
                if event is None:
                    break
                key = f"outbox:{event.id}"
                Job.objects.get_or_create(
                    idempotency_key=key,
                    defaults={
                        "job_type": event.event_type,
                        "payload_version": event.event_version,
                        "payload": validate_identifier_payload(event.payload),
                        "available_at": now,
                    },
                )
                event.published_at = now
                event.save(update_fields=["published_at", "updated_at"])
            dispatched += 1
        return dispatched


class JobRuntime:
    """Claims leased jobs and persists terminal/retry outcomes safely."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        clock: Clock | None = None,
        lease_seconds: int = 60,
        telemetry: TelemetryPort | None = None,
        cache: CacheInvalidationPort | None = None,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.clock = clock or _SystemClock()
        self.lease_seconds = lease_seconds
        self.telemetry = telemetry
        self.cache = cache
        self.handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        if not job_type or job_type in self.handlers:
            raise ValueError("job type must be unique and non-empty")
        self.handlers[job_type] = handler

    def enqueue(
        self,
        *,
        job_type: str,
        payload: Mapping[str, Any] | None = None,
        idempotency_key: str,
        priority: int = 50,
        max_attempts: int = 5,
        available_at: datetime | None = None,
    ) -> Job:
        safe_payload = validate_identifier_payload(payload or {})
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key must be non-empty and bounded")
        if max_attempts < 1 or priority < 0:
            raise ValueError("invalid job limits")
        when = (available_at or self.clock.now()).astimezone(UTC)
        try:
            with transaction.atomic():
                job = Job.objects.create(
                    job_type=job_type,
                    payload_version=1,
                    payload=safe_payload,
                    priority=priority,
                    max_attempts=max_attempts,
                    available_at=when,
                    idempotency_key=idempotency_key,
                )
        except IntegrityError:
            existing = Job.objects.get(idempotency_key=idempotency_key)
            if existing.job_type != job_type or _payload_hash(existing.payload) != _payload_hash(
                safe_payload
            ):
                raise JobConflictError("idempotency key was already used for another job") from None
            return existing
        return job

    def recover_expired(self) -> int:
        now = self.clock.now()
        return Job.objects.filter(
            state=JobState.RUNNING, lease_expires_at__isnull=False, lease_expires_at__lte=now
        ).update(
            state=JobState.PENDING,
            available_at=now,
            leased_at=None,
            lease_expires_at=None,
            worker_id="",
        )

    def claim(self) -> ClaimedJob | None:
        now = self.clock.now()
        self.recover_expired()
        with transaction.atomic():
            job = (
                Job.objects.select_for_update(skip_locked=True)
                .filter(state=JobState.PENDING, available_at__lte=now)
                .order_by("-priority", "available_at", "id")
                .first()
            )
            if job is None:
                return None
            job.state = JobState.RUNNING
            job.attempts += 1
            job.leased_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.worker_id = self.worker_id
            job.save(
                update_fields=[
                    "state",
                    "attempts",
                    "leased_at",
                    "lease_expires_at",
                    "worker_id",
                    "updated_at",
                ]
            )
            return ClaimedJob(
                job.id,
                job.job_type,
                dict(job.payload),
                job.attempts,
                job.max_attempts,
                job.idempotency_key,
            )

    def run_once(self) -> bool:
        claimed = self.claim()
        if claimed is None:
            return False
        try:
            handler = self.handlers.get(claimed.job_type)
            cache_default = claimed.job_type == "CACHE_INVALIDATE" and self.cache is not None
            if handler is None and cache_default:

                def _default_cache_invalidate(payload: dict[str, Any]) -> dict[str, Any]:
                    return {"cache_key": payload["cache_key"]}

                handler = _default_cache_invalidate
            if handler is None:
                raise TerminalJobError("unknown_job_type")
            outcome = dict(handler(claimed.payload) or {})
            safe_outcome = validate_identifier_payload(outcome)
        except RetryableJobError as exc:
            self._failure(claimed, exc.category, retry=True)
        except TerminalJobError as exc:
            self._failure(claimed, exc.category, retry=False)
        except KeyError:
            self._failure(claimed, "terminal_failure", retry=False)
        except Exception:
            self._failure(claimed, "handler_error", retry=True)
        else:
            self._success(claimed, safe_outcome)
        return True

    def _success(self, claimed: ClaimedJob, outcome: dict[str, Any]) -> None:
        now = self.clock.now()
        updated = Job.objects.filter(
            id=claimed.id, state=JobState.RUNNING, worker_id=self.worker_id
        ).update(
            state=JobState.SUCCEEDED,
            terminal_outcome={"status": "SUCCEEDED", **outcome},
            completed_at=now,
            leased_at=None,
            lease_expires_at=None,
            worker_id="",
            updated_at=now,
        )
        if updated and self.telemetry:
            self.telemetry.increment("jobs.succeeded", attributes={"job_type": claimed.job_type})
        if updated and self.cache and claimed.job_type == "CACHE_INVALIDATE":
            cache_key = outcome.get("cache_key") or claimed.payload.get("cache_key")
            if isinstance(cache_key, str):
                self.cache.invalidate(cache_key)

    def _failure(self, claimed: ClaimedJob, category: str, *, retry: bool) -> None:
        now = self.clock.now()
        terminal = not retry or claimed.attempts >= claimed.max_attempts
        state = JobState.DEAD if terminal else JobState.PENDING
        delay = min(3600, 2 ** max(0, claimed.attempts - 1))
        updates: dict[str, Any] = {
            "state": state,
            "last_error_category": category[:100],
            "leased_at": None,
            "lease_expires_at": None,
            "worker_id": "",
            "updated_at": now,
        }
        if terminal:
            updates.update(
                {
                    "terminal_outcome": {"status": "DEAD", "error_category": category[:100]},
                    "completed_at": now,
                }
            )
        else:
            updates["available_at"] = now + timedelta(seconds=delay)
        Job.objects.filter(id=claimed.id, state=JobState.RUNNING, worker_id=self.worker_id).update(
            **updates
        )
        if self.telemetry:
            self.telemetry.increment(
                "jobs.failed",
                attributes={"job_type": claimed.job_type, "terminal": str(terminal).lower()},
            )


class _SystemClock:
    def now(self) -> datetime:
        return django_timezone.now()
