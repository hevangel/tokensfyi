from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import pytest

from token_budget_platform.adapters.backup import LocalBackupGate
from token_budget_platform.adapters.clock import FrozenClock
from token_budget_platform.adapters.key_management import LocalKeyManagement
from token_budget_platform.adapters.object_storage import LocalObjectStore
from token_budget_platform.adapters.scanner import LocalMalwareScanner
from token_budget_platform.adapters.telemetry import LocalCacheInvalidation, LocalTelemetry
from token_budget_platform.application.jobs import (
    JobConflictError,
    JobPayloadError,
    JobRuntime,
    RetryableJobError,
    TransactionalOutbox,
    validate_identifier_payload,
)
from token_budget_platform.domain.ports import ObjectMetadata


def test_local_adapters_preserve_port_contracts() -> None:
    store = LocalObjectStore()
    store.put_encrypted("quarantine/evidence", BytesIO(b"safe"), ObjectMetadata(byte_count=4))
    with store.open_encrypted("quarantine/evidence") as stream:
        assert stream.read() == b"safe"
    assert LocalMalwareScanner().scan(BytesIO(b"safe")).clean
    assert not LocalMalwareScanner().scan(BytesIO(b"EICAR")).clean

    key_manager = LocalKeyManagement(b"k" * 32)
    wrapped = key_manager.generate_data_key("evidence")
    assert len(key_manager.decrypt_data_key(wrapped)) == 32
    key_manager.destroy_reference(wrapped.key_id)
    with pytest.raises(KeyError):
        key_manager.decrypt_data_key(wrapped)

    gate = LocalBackupGate()
    assert gate.mutation_allowed().allowed
    assert not gate.set_decision(False, reason_code="BACKUP_FAILED").allowed


def test_identifier_payload_rejects_private_content() -> None:
    identifier = uuid4()
    assert validate_identifier_payload({"record_id": identifier}) == {"record_id": str(identifier)}
    assert validate_identifier_payload({"evidence_id": identifier}) == {
        "evidence_id": str(identifier)
    }
    with pytest.raises(JobPayloadError):
        validate_identifier_payload({"record_id": identifier, "email": "private@example.invalid"})
    with pytest.raises(JobPayloadError):
        validate_identifier_payload({"record_id": identifier, "free_text": "not an identifier"})


@pytest.mark.django_db
def test_outbox_dispatch_is_atomic_and_idempotent() -> None:
    aggregate_id = uuid4()
    outbox = TransactionalOutbox()
    event = outbox.enqueue(
        aggregate_type="record",
        aggregate_id=aggregate_id,
        event_type="CACHE_INVALIDATE",
        payload={"record_id": aggregate_id, "cache_key": "record:1"},
    )
    assert outbox.dispatch() == 1
    assert outbox.dispatch() == 0
    from token_budget_platform.adapters.django.models import Job, OutboxEvent

    assert Job.objects.count() == 1
    assert OutboxEvent.objects.get(id=event.id).published_at is not None


@pytest.mark.django_db
def test_worker_retries_then_persists_terminal_success_and_cache_invalidation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(now)
    telemetry = LocalTelemetry()
    cache = LocalCacheInvalidation()
    runtime = JobRuntime(clock=clock, telemetry=telemetry, cache=cache, lease_seconds=10)
    calls = 0

    def handler(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableJobError("temporary")
        return {"cache_key": payload["cache_key"]}

    runtime.register("CACHE_INVALIDATE", handler)
    job = runtime.enqueue(
        job_type="CACHE_INVALIDATE",
        payload={"cache_key": "record:1"},
        idempotency_key="job-1",
        max_attempts=2,
    )
    assert runtime.run_once()
    clock.advance(seconds=1)
    assert runtime.run_once()
    job.refresh_from_db()
    assert job.state == "SUCCEEDED"
    assert job.terminal_outcome["status"] == "SUCCEEDED"
    assert cache.invalidated == ["record:1"]
    assert len(telemetry.metrics) == 2

    assert (
        runtime.enqueue(
            job_type="CACHE_INVALIDATE", payload={"cache_key": "record:1"}, idempotency_key="job-1"
        ).id
        == job.id
    )
    with pytest.raises(JobConflictError):
        runtime.enqueue(
            job_type="CACHE_INVALIDATE", payload={"cache_key": "record:2"}, idempotency_key="job-1"
        )


@pytest.mark.django_db
def test_worker_reclaims_expired_lease_and_marks_dead_after_max_attempts() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(now)
    runtime = JobRuntime(clock=clock, lease_seconds=1)
    runtime.register("FAIL", lambda payload: (_ for _ in ()).throw(RetryableJobError("broken")))
    runtime.enqueue(
        job_type="FAIL", payload={"record_id": uuid4()}, idempotency_key="dead", max_attempts=1
    )
    assert runtime.run_once()
    from token_budget_platform.adapters.django.models import Job

    job = Job.objects.get(idempotency_key="dead")
    assert job.state == "DEAD"
    assert job.terminal_outcome["error_category"] == "broken"
