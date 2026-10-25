"""Staff API endpoints: resource-scoped moderator evidence access (Req 6.2, 10.7).

Access requires BOTH an authenticated Moderator/Administrator session AND a
CLEAN scan state.  Responses stream decrypted bytes with a safe attachment
filename; internal object keys and storage URLs are never exposed.
"""

from __future__ import annotations

from uuid import UUID

from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseBase
from django.views.decorators.http import require_GET

from token_budget_platform.application.evidence import EvidenceService
from token_budget_platform.application.iam.authorization import AuthorizationService
from token_budget_platform.domain.iam.policies import Resource
from token_budget_platform.transports.html.views import _staff_identity


def _evidence_service() -> EvidenceService:
    from django.conf import settings

    from token_budget_platform.adapters.clock import SystemClock
    from token_budget_platform.adapters.key_management import LocalKeyManagement
    from token_budget_platform.adapters.object_storage import LocalObjectStore
    from token_budget_platform.adapters.scanner import LocalMalwareScanner

    material = str(settings.SECRET_KEY).encode("utf-8")
    return EvidenceService(
        object_store=LocalObjectStore(),
        key_management=LocalKeyManagement(material[:32].ljust(32, b"0")),
        scanner=LocalMalwareScanner(),
        clock=SystemClock(),
    )


@require_GET
def staff_evidence_download(request: HttpRequest, evidence_id) -> HttpResponseBase:
    """Stream decrypted clean evidence to an authorized moderator."""
    identity = _staff_identity(request)
    if identity is None:
        return HttpResponse("Authentication required.", status=401)
    decision = AuthorizationService().authorize(
        actor_username=identity["username"],
        resource=Resource.EVIDENCE_ACCESS,
    )
    if not decision.allowed:
        return HttpResponse("Forbidden.", status=403)

    try:
        evidence_uuid = evidence_id if isinstance(evidence_id, UUID) else UUID(str(evidence_id))
    except (ValueError, AttributeError):
        return HttpResponse("Not found.", status=404)

    allowed, filename, mime_type, content = _evidence_service().read_for_moderator(evidence_uuid)
    if not allowed:
        # Denied uniformly: unknown ids, pending/errored/quarantined scans.
        return HttpResponse("Not found.", status=404)

    safe_name = (
        "".join(char for char in filename if char.isalnum() or char in "._-")[:120]
        or "evidence.bin"
    )
    response = FileResponse(
        _bytes_iterator(content),
        content_type=mime_type or "application/octet-stream",
    )
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _bytes_iterator(content: bytes):
    yield content
