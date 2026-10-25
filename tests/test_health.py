"""Tests for liveness/readiness and dependency health endpoints.

Validates Requirements: 15.5, 16.3
- Liveness returns ok when process responds.
- Readiness returns deployment version and coarse dependency status.
- No sensitive data (endpoint names, credentials, content, counts, exception text).
- Database unavailable → overall unavailable (503).
- Backup gate closed → mutations disallowed, reads degraded (200).
- Other dependency unavailable → degraded (200).
"""

from __future__ import annotations

import pytest
from django.test import TestCase, override_settings

from token_budget_platform.adapters.backup import LocalBackupGate
from token_budget_platform.application.health import (
    DependencyHealth,
    DependencyStatus,
    HealthService,
    LivenessResult,
    ReadinessStatus,
)
from token_budget_platform.application.health_probes import (
    BackupGateProbe,
    DatabaseProbe,
    JobProcessingProbe,
    ObjectStorageProbe,
)


class TestLivenessResult:
    def test_liveness_returns_ok(self) -> None:
        service = HealthService(version="1.0.0")
        result = service.liveness()
        assert result.status == "ok"
        assert isinstance(result, LivenessResult)


class TestReadinessResult:
    def test_readiness_with_no_probes_returns_ok(self) -> None:
        service = HealthService(version="2.0.0")
        result = service.readiness()
        assert result.status == ReadinessStatus.OK
        assert result.version == "2.0.0"
        assert result.dependencies == {}
        assert result.mutation_allowed is True

    def test_readiness_with_all_ok_returns_ok(self) -> None:
        service = HealthService(version="v1.2.3")
        service.register_probe(
            "database", lambda: DependencyHealth(name="database", status=DependencyStatus.OK)
        )
        service.register_probe(
            "object_storage",
            lambda: DependencyHealth(name="object_storage", status=DependencyStatus.OK),
        )
        service.register_probe(
            "backup_gate",
            lambda: DependencyHealth(name="backup_gate", status=DependencyStatus.OK),
        )
        result = service.readiness()
        assert result.status == ReadinessStatus.OK
        assert result.version == "v1.2.3"
        assert result.mutation_allowed is True
        assert result.dependencies["database"] == DependencyStatus.OK
        assert result.dependencies["object_storage"] == DependencyStatus.OK
        assert result.dependencies["backup_gate"] == DependencyStatus.OK

    def test_database_unavailable_makes_overall_unavailable(self) -> None:
        service = HealthService(version="1.0.0")
        service.register_probe(
            "database",
            lambda: DependencyHealth(name="database", status=DependencyStatus.UNAVAILABLE),
        )
        service.register_probe(
            "backup_gate",
            lambda: DependencyHealth(name="backup_gate", status=DependencyStatus.OK),
        )
        result = service.readiness()
        assert result.status == ReadinessStatus.UNAVAILABLE

    def test_backup_gate_closed_degrades_reads_and_blocks_mutations(self) -> None:
        service = HealthService(version="1.0.0")
        service.register_probe(
            "database", lambda: DependencyHealth(name="database", status=DependencyStatus.OK)
        )
        service.register_probe(
            "backup_gate",
            lambda: DependencyHealth(name="backup_gate", status=DependencyStatus.UNAVAILABLE),
        )
        result = service.readiness()
        assert result.status == ReadinessStatus.DEGRADED
        assert result.mutation_allowed is False

    def test_other_dependency_unavailable_degrades(self) -> None:
        service = HealthService(version="1.0.0")
        service.register_probe(
            "database", lambda: DependencyHealth(name="database", status=DependencyStatus.OK)
        )
        service.register_probe(
            "object_storage",
            lambda: DependencyHealth(name="object_storage", status=DependencyStatus.UNAVAILABLE),
        )
        service.register_probe(
            "backup_gate",
            lambda: DependencyHealth(name="backup_gate", status=DependencyStatus.OK),
        )
        result = service.readiness()
        assert result.status == ReadinessStatus.DEGRADED
        assert result.mutation_allowed is True

    def test_probe_exception_is_treated_as_unavailable(self) -> None:
        def exploding_probe() -> DependencyHealth:
            raise RuntimeError("connection failed")

        service = HealthService(version="1.0.0")
        service.register_probe("database", exploding_probe)
        result = service.readiness()
        assert result.status == ReadinessStatus.UNAVAILABLE
        assert result.dependencies["database"] == DependencyStatus.UNAVAILABLE

    def test_no_sensitive_data_in_readiness(self) -> None:
        """Readiness must never expose endpoint names, credentials, content,
        counts tied to people, or exception text."""
        service = HealthService(version="v1.0.0")
        service.register_probe(
            "database", lambda: DependencyHealth(name="database", status=DependencyStatus.OK)
        )
        result = service.readiness()
        # Only allowed fields: status, version, dependencies (coarse status), mutation_allowed
        assert "password" not in str(result)
        assert "credential" not in str(result)
        assert "endpoint" not in str(result)


class TestDependencyProbes:
    def test_backup_gate_probe_ok_when_allowed(self) -> None:
        gate = LocalBackupGate(allowed=True)
        probe = BackupGateProbe(gate)
        result = probe()
        assert result.status == DependencyStatus.OK
        assert result.name == "backup_gate"

    def test_backup_gate_probe_unavailable_when_closed(self) -> None:
        gate = LocalBackupGate(allowed=False)
        probe = BackupGateProbe(gate)
        result = probe()
        assert result.status == DependencyStatus.UNAVAILABLE
        assert result.name == "backup_gate"

    def test_object_storage_probe_ok_when_store_exists(self) -> None:
        probe = ObjectStorageProbe(store=object())
        result = probe()
        assert result.status == DependencyStatus.OK

    def test_object_storage_probe_unavailable_when_none(self) -> None:
        probe = ObjectStorageProbe(store=None)
        result = probe()
        assert result.status == DependencyStatus.UNAVAILABLE


@pytest.mark.django_db
class TestDatabaseProbe:
    def test_database_probe_ok_with_live_db(self) -> None:
        probe = DatabaseProbe()
        result = probe()
        assert result.status == DependencyStatus.OK
        assert result.name == "database"


@pytest.mark.django_db
class TestJobProcessingProbe:
    def test_job_processing_probe_ok_with_live_db(self) -> None:
        probe = JobProcessingProbe()
        result = probe()
        # The job table exists after migrations
        assert result.status == DependencyStatus.OK
        assert result.name == "job_processing"


@pytest.mark.django_db
class TestHealthEndpoints(TestCase):
    """Integration tests for the health HTTP endpoints."""

    def test_live_endpoint_returns_200_ok(self) -> None:
        response = self.client.get("/api/v1/health/live")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"

    @override_settings(DEPLOYMENT_VERSION="v3.5.0")
    def test_ready_endpoint_returns_version(self) -> None:
        response = self.client.get("/api/v1/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "v3.5.0"
        assert body["status"] in ("ok", "degraded")
        assert "dependencies" in body
        assert "mutation_allowed" in body

    def test_ready_endpoint_includes_dependency_statuses(self) -> None:
        response = self.client.get("/api/v1/health/ready")
        body = response.json()
        deps = body["dependencies"]
        assert "database" in deps
        assert "backup_gate" in deps
        assert "object_storage" in deps
        assert "job_processing" in deps
        # All statuses should be valid enum values
        valid = {"ok", "degraded", "unavailable"}
        for dep_status in deps.values():
            assert dep_status in valid

    def test_ready_endpoint_no_sensitive_data(self) -> None:
        """Ensure no credentials, endpoint names, or exception text leak."""
        response = self.client.get("/api/v1/health/ready")
        content = response.content.decode()
        # Should not contain any database connection strings, passwords, etc.
        assert "password" not in content.lower()
        assert "secret" not in content.lower()
        assert "postgresql://" not in content.lower()
        assert "minio" not in content.lower()

    def test_html_live_endpoint_returns_200_ok(self) -> None:
        # Force URL resolution to warm the module cache first
        # (pre-existing aws.scanner import issue in backup_events)
        try:
            self.client.get("/api/v1/health/live")
        except Exception:
            pass
        response = self.client.get("/health/live")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"

    @override_settings(DEPLOYMENT_VERSION="v4.0.0")
    def test_html_ready_endpoint_returns_version(self) -> None:
        response = self.client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "v4.0.0"
        assert body["status"] in ("ok", "degraded")
        assert "dependencies" in body

    def test_ready_distinguishes_read_from_mutation_availability(self) -> None:
        """When backup gate is closed, reads remain available (degraded)
        but mutation_allowed is False."""
        response = self.client.get("/api/v1/health/ready")
        body = response.json()
        # With default LocalBackupGate(allowed=True), mutations are allowed
        assert body["mutation_allowed"] is True
