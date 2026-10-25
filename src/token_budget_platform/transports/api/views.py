"""API transport health endpoints.

Requirements: 15.5, 16.3
- /health/live: Process liveness only.
- /health/ready: Readiness, deployment version, coarse dependency status; no sensitive data.
"""

from django.conf import settings
from django.http import JsonResponse

from token_budget_platform.application.health import HealthService, ReadinessStatus
from token_budget_platform.application.health_probes import (
    BackupGateProbe,
    DatabaseProbe,
    JobProcessingProbe,
    ObjectStorageProbe,
)


def _get_health_service() -> HealthService:
    """Build a HealthService with registered dependency probes."""
    version = getattr(settings, "DEPLOYMENT_VERSION", "local-dev")
    service = HealthService(version=version)

    # Register probes for each dependency
    service.register_probe("database", DatabaseProbe())
    service.register_probe("job_processing", JobProcessingProbe())

    # Object storage probe — use the local adapter if available
    # In production, this would use the S3 adapter
    service.register_probe("object_storage", ObjectStorageProbe(store=True))

    # Backup gate probe — use the local adapter
    from token_budget_platform.adapters.backup import LocalBackupGate

    _backup_gate = getattr(settings, "BACKUP_GATE", None) or LocalBackupGate()
    service.register_probe("backup_gate", BackupGateProbe(_backup_gate))

    return service


def health_live(request) -> JsonResponse:
    """Liveness: proves only that the process event loop responds."""
    service = _get_health_service()
    result = service.liveness()
    return JsonResponse({"status": result.status})


def health_ready(request) -> JsonResponse:
    """Readiness: deployment version and coarse dependency status.

    Returns HTTP 200 for ok/degraded, HTTP 503 for unavailable.
    Never exposes endpoint names, credentials, content, counts, or exception text.
    """
    service = _get_health_service()
    result = service.readiness()

    body = {
        "status": result.status.value,
        "version": result.version,
        "dependencies": {name: status.value for name, status in result.dependencies.items()},
        "mutation_allowed": result.mutation_allowed,
    }

    if result.status == ReadinessStatus.UNAVAILABLE:
        return JsonResponse(body, status=503)
    return JsonResponse(body, status=200)
