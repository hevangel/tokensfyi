"""API endpoint for exchanging a capability token for a scoped management session.

POST /api/v1/management-sessions

The client sends the 256-bit fragment token (extracted from the URL hash by
a first-party script). The server validates, issues a Secure/HttpOnly/SameSite=Strict
session cookie, and returns a redirect URL.

All failure modes produce the same non-enumerating response shape to prevent
information leakage about whether a submission exists.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from token_budget_platform.application.submission.capability_service import (
    CapabilityService,
    ExchangeOutcome,
)
from token_budget_platform.transports.api.problems import (
    PROBLEM_TYPE_FORBIDDEN,
    problem_response,
)

logger = logging.getLogger(__name__)

# Management session cookie name
SESSION_COOKIE_NAME = "mgmt_session"


@csrf_exempt
@require_POST
def exchange_capability(request: HttpRequest) -> JsonResponse:
    """Exchange a capability token for a scoped management session.

    Request body: {"token": "<64-char hex string>"}
    Success: 201 with Set-Cookie and redirect_url
    Failure: 403 with non-enumerating denial message (same for all failure types)
    """
    # Parse request body
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _non_enumerating_denial()

    token = body.get("token", "")
    if not isinstance(token, str) or not token:
        return _non_enumerating_denial()

    # Get the service
    service = get_capability_service()

    # Exchange
    outcome: ExchangeOutcome = service.exchange_capability(token)

    if not outcome.success or outcome.session is None:
        return _non_enumerating_denial()

    # Build success response
    response_body: dict = {
        "redirect_url": "/manage/session/",
    }

    # Include rotated token if capability was rotated
    if outcome.rotated_token:
        response_body["rotated_management_link"] = f"/manage/#capability={outcome.rotated_token}"

    response = JsonResponse(response_body, status=201)

    # Set secure session cookie
    now = service._clock.now()
    max_age = int((outcome.session.expires_at - now).total_seconds())
    response.set_cookie(
        SESSION_COOKIE_NAME,
        outcome.session.session_secret,
        max_age=max_age,
        secure=getattr(settings, "SESSION_COOKIE_SECURE", True),
        httponly=True,
        samesite="Strict",
        path="/manage/",
    )

    # Never log the session secret or capability token
    logger.info(
        "management_session_created",
        extra={
            "record_id": str(outcome.session.record_id),
            "session_id": str(outcome.session.session_id),
        },
    )

    return response


def _non_enumerating_denial() -> JsonResponse:
    """Return a uniform denial that reveals nothing about submission existence."""
    return problem_response(
        status=403,
        title="Access Denied",
        problem_type=PROBLEM_TYPE_FORBIDDEN,
        detail=(
            "The management link is invalid or has expired. "
            "If you previously provided an email address, you can request "
            "a recovery link from the submission management page."
        ),
    )


# ──── Service wiring ───────────────────────────────────────────────────────────

_capability_service: CapabilityService | None = None


def get_capability_service() -> CapabilityService:
    """Lazily construct and cache the CapabilityService singleton."""
    global _capability_service
    if _capability_service is not None:
        return _capability_service

    from token_budget_platform.adapters.clock import SystemClock
    from token_budget_platform.adapters.hmac_keys import LocalHmacKeyProvider
    from token_budget_platform.adapters.postgresql.capability_repository import (
        DjangoCapabilityRepository,
    )

    # In production the master key would come from KMS/Secrets Manager.
    # Locally it comes from the CAPABILITY_HMAC_KEY setting or a generated default.
    hmac_key_hex = getattr(settings, "CAPABILITY_HMAC_KEY", None)
    if hmac_key_hex:
        master_key = bytes.fromhex(hmac_key_hex)
    else:
        # Development fallback: deterministic key for local runs
        master_key = b"local-dev-capability-hmac-key-32"

    _capability_service = CapabilityService(
        clock=SystemClock(),
        key_provider=LocalHmacKeyProvider(master_key),
        repository=DjangoCapabilityRepository(),
    )
    return _capability_service


def reset_capability_service() -> None:
    """Reset the cached service (used in tests)."""
    global _capability_service
    _capability_service = None
