"""HTML transport views for every core journey.

The server-rendered pages are authoritative: each form works without
JavaScript, and POST bodies are never logged (sanitized telemetry).
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from token_budget_platform import __version__

MANAGEMENT_SESSION_COOKIE = "tbp_management_session"
STAFF_SESSION_COOKIE = "tbp_staff_session"


def home(request: HttpRequest):
    from token_budget_platform.application.catalog.queries import PublicRecordQuery

    summary = PublicRecordQuery().get_dataset_summary()
    return render(
        request,
        "home.html",
        {
            "version": __version__,
            "published_count": summary.published_count,
            "last_updated": summary.last_updated,
        },
    )


# ──── Public catalog journeys ─────────────────────────────────────────────────


def _page_number(request: HttpRequest) -> int:
    raw = str(request.GET.get("page", "1") or "1")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def browse(request: HttpRequest) -> HttpResponse:
    """Browse and search the public catalog with filters, sort, and counts."""
    from token_budget_platform.application.catalog.search import parse_filters, search_records

    filters, errors = parse_filters(request.GET)
    page = search_records(filters, page=_page_number(request))
    if errors:
        page = replace(page, filter_errors=errors)
    return render(request, "browse.html", {"page": page, "filter_options": _filter_options()})


def _filter_options() -> dict[str, list[dict[str, str]]]:
    from token_budget_platform.adapters.django.models import BudgetScope, VerificationStatus
    from token_budget_platform.application.catalog.search import active_controlled_value_options

    options = active_controlled_value_options()
    options["budget_scope"] = [
        {"label": choice.label, "value": choice.value} for choice in BudgetScope
    ]
    options["verification_status"] = [
        {"label": choice.label, "value": choice.value} for choice in VerificationStatus
    ]
    return options


def organization(request: HttpRequest, slug: str) -> HttpResponse:
    """Canonical organization profile with disclosed aggregates."""
    from token_budget_platform.application.catalog.search import organization_profile

    profile = organization_profile(slug)
    if profile is None:
        return render(request, "not_found.html", status=404)
    return render(request, "organization.html", {"profile": profile})


def record(request: HttpRequest, record_id) -> HttpResponse:
    """One publicly eligible record by public reference."""
    from token_budget_platform.application.catalog.search import record_detail

    try:
        reference = record_id if isinstance(record_id, UUID) else UUID(str(record_id))
    except (ValueError, AttributeError):
        return render(request, "not_found.html", status=404)
    detail = record_detail(reference)
    if detail is None:
        return render(request, "not_found.html", status=404)
    return render(request, "record_detail.html", {"detail": detail})


def compare(request: HttpRequest) -> HttpResponse:
    """Aligned comparison of two through five records (ids=ref1,ref2,...)."""
    from token_budget_platform.application.catalog.search import (
        ComparisonError,
        comparison,
        parse_comparison_references,
    )

    raw_ids = str(request.GET.get("ids", ""))
    try:
        references = parse_comparison_references(raw_ids)
        result = comparison(references)
    except ComparisonError as exc:
        return render(
            request,
            "compare.html",
            {"error": str(exc), "raw_ids": raw_ids},
            status=400,
        )
    return render(request, "compare.html", {"comparison": result, "raw_ids": raw_ids})


# ──── Contributor submission journey ──────────────────────────────────────────


def submission_start(request: HttpRequest) -> HttpResponse:
    """Submission form; POST validates and shows the exact public preview."""
    from token_budget_platform.application.catalog.search import active_controlled_value_options
    from token_budget_platform.application.submission.service import SubmissionService

    if request.method == "GET":
        return render(
            request,
            "submission_start.html",
            {"filter_options": active_controlled_value_options()},
        )

    payload = _payload_from_post(request)
    service = SubmissionService()
    result = service.create_draft(payload)
    validation = result.validation_result
    if not validation.get("valid"):
        return render(
            request,
            "submission_start.html",
            {
                "filter_options": active_controlled_value_options(),
                "errors": validation.get("errors", []),
                "submitted": payload,
            },
            status=400,
        )

    from token_budget_platform.application.catalog.public_projection_compat import build_preview

    return render(
        request,
        "submission_preview.html",
        {
            "preview": build_preview(payload),
            "draft_id": str(result.draft_id),
            "capability_token": result.capability_token,
        },
    )


@csrf_protect
@require_POST
def submission_confirm(request: HttpRequest) -> HttpResponse:
    """Atomically confirm the draft; the management link is shown exactly once."""
    from token_budget_platform.application.submission.confirmation import (
        ConfirmationError,
        SubmissionConfirmationService,
    )

    draft_id = str(request.POST.get("draft_id", ""))
    capability_token = str(request.POST.get("capability_token", ""))
    idempotency_key = str(request.POST.get("idempotency_key", "") or "")

    try:
        draft_uuid = UUID(draft_id)
    except ValueError:
        return render(request, "not_found.html", status=404)

    service = SubmissionConfirmationService()
    try:
        result = service.confirm(
            draft_id=draft_uuid,
            draft_capability_token=capability_token,
            requester_scope="submission-web",
            idempotency_key=idempotency_key,
        )
    except ConfirmationError as exc:
        return render(
            request,
            "submission_start.html",
            {"errors": [{"field": "confirmation", "message": str(exc)}], "submitted": {}},
            status=400,
        )

    return render(
        request,
        "submission_confirmed.html",
        {
            "result": result,
            "manage_url": f"/submit/manage/{result.management_token}",
        },
    )


def submission_landing(request: HttpRequest, token: str) -> HttpResponse:
    """Management-link landing: exchanges the token, then redirects to a clean URL."""
    from token_budget_platform.application.submission.confirmation import (
        management_capability_service,
    )

    service = management_capability_service()
    outcome = service.exchange_capability(token)
    if not outcome.success or outcome.session is None:
        return render(request, "manage_denied.html", status=403)

    response = redirect("manage-session")
    response.set_cookie(
        MANAGEMENT_SESSION_COOKIE,
        outcome.session.session_secret,
        max_age=30 * 60,
        httponly=True,
        samesite="Lax",
    )
    return response


def community_report(request: HttpRequest) -> HttpResponse:
    """Community report form; sensitive categories suppress synchronously."""
    from token_budget_platform.application.moderation.service import (
        ModerationError,
        ModerationService,
    )
    from token_budget_platform.domain.moderation.reports import ReportCategory

    category_options = [
        {"value": category.value, "label": category.value.replace("_", " ").title()}
        for category in ReportCategory
    ]
    reference = str(request.GET.get("record", request.POST.get("record_reference", "")) or "")

    if request.method == "GET":
        return render(
            request,
            "community_report.html",
            {"record_reference": reference, "categories": category_options},
        )

    category = str(request.POST.get("category", ""))
    explanation = str(request.POST.get("explanation", ""))
    service = ModerationService()
    try:
        outcome = service.file_report(
            record_reference=UUID(reference) if reference else None,
            category=category,
            explanation=explanation,
        )
    except (ModerationError, ValueError) as exc:
        return render(
            request,
            "community_report.html",
            {
                "record_reference": reference,
                "categories": category_options,
                "errors": [{"field": "report", "message": str(exc)}],
            },
            status=400,
        )

    return render(
        request,
        "community_report_received.html",
        {"outcome": outcome},
    )


# ──── Contributor management journey ──────────────────────────────────────────


def manage_landing(request: HttpRequest) -> HttpResponse:
    """Management link landing page with script-based exchange and noscript recovery."""
    if request.method == "GET":
        return render(request, "manage_landing.html")

    if str(request.POST.get("action", "")) == "recover":
        return _handle_recovery_request(request)

    token = str(request.POST.get("token", ""))
    from token_budget_platform.application.submission.confirmation import (
        management_capability_service,
    )

    service = management_capability_service()
    outcome = service.exchange_capability(token)
    if not outcome.success or outcome.session is None:
        return render(request, "manage_denied.html", status=403)

    response = redirect("manage-session")
    response.set_cookie(
        MANAGEMENT_SESSION_COOKIE,
        outcome.session.session_secret,
        max_age=30 * 60,
        httponly=True,
        samesite="Lax",
    )
    return response


def _handle_recovery_request(request: HttpRequest) -> HttpResponse:
    """Non-enumerating recovery: identical response whichever branch executes."""
    from uuid import UUID as _UUID

    from token_budget_platform.application.submission.management_service import (
        ContributorManagementService,
    )

    email = str(request.POST.get("recovery_email", ""))
    reference = str(request.POST.get("recovery_reference", ""))
    try:
        reference_uuid = _UUID(reference)
    except ValueError:
        reference_uuid = None
    ContributorManagementService().request_recovery(
        submitted_email=email, public_reference=reference_uuid
    )
    return render(request, "manage_recovery_sent.html")


def _management_record_id(request: HttpRequest) -> UUID | None:
    """Resolve the management session cookie to its record id, or None."""
    from token_budget_platform.application.submission.confirmation import (
        management_capability_service,
    )

    secret = request.COOKIES.get(MANAGEMENT_SESSION_COOKIE, "")
    if not secret:
        return None
    service = management_capability_service()
    session = service.validate_session(secret)
    return session.record_id if session else None


def manage_session(request: HttpRequest) -> HttpResponse:
    """Authenticated management session: status, amendment, withdrawal."""
    from token_budget_platform.application.submission.management_service import (
        ContributorManagementService,
        ManagementError,
    )

    record_id = _management_record_id(request)
    if record_id is None:
        return render(request, "manage_denied.html", status=403)

    service = ContributorManagementService()
    error_message = None
    outcome_message = None

    if request.method == "POST":
        action = str(request.POST.get("action", ""))
        try:
            if action == "withdraw":
                service.withdraw(record_id=record_id)
                outcome_message = "Submission withdrawn; it is no longer publicly visible."
            elif action == "amend":
                changes = _amendment_changes(request)
                result = service.amend(record_id=record_id, changes=changes)
                outcome_message = (
                    f"Amendment recorded as version {result.new_version}; "
                    "the submission re-entered review."
                )
        except (ManagementError, ValueError) as exc:
            error_message = str(exc)

    try:
        status = service.status(record_id=record_id)
    except ManagementError:
        return render(request, "manage_denied.html", status=403)

    return render(
        request,
        "manage_session.html",
        {
            "status": status,
            "error": error_message,
            "outcome": outcome_message,
        },
    )


def _amendment_changes(request: HttpRequest) -> dict[str, object]:
    changes: dict[str, object] = {}
    for field in (
        "token_budget",
        "input_tokens",
        "output_tokens",
        "token_usage",
        "cost_minor_units",
        "cost_currency",
        "cost_basis",
        "seat_count",
        "workload_count",
        "reporting_start",
        "reporting_end",
        "reporting_timezone",
        "source_date",
        "methodology_notes",
    ):
        value = str(request.POST.get(f"amend_{field}", "") or "").strip()
        if value:
            changes[field] = value
    return changes


# ──── Staff journeys ──────────────────────────────────────────────────────────


def staff_login(request: HttpRequest) -> HttpResponse:
    """Staff login: Argon2id password + TOTP/recovery code; exact lockout."""
    import django.core.signing as signing
    from django.conf import settings

    from token_budget_platform.application.iam.authentication import AuthenticationService

    error_message = None
    if request.method == "POST":
        service = AuthenticationService()
        result = service.authenticate(
            username=str(request.POST.get("username", "")),
            password=str(request.POST.get("password", "")),
            totp_code=str(request.POST.get("totp_code", "")),
            recovery_code=str(request.POST.get("recovery_code", "")),
        )
        if result.success and result.session is not None:
            cookie_value = signing.dumps(
                {
                    "username": result.session.username,
                    "epoch": result.session.session_revocation_epoch,
                    "role": result.session.role.value,
                },
                key=str(settings.SECRET_KEY),
            )
            response = redirect(
                "moderation" if result.session.role.value == "MODERATOR" else "administration"
            )
            response.set_cookie(
                STAFF_SESSION_COOKIE,
                cookie_value,
                max_age=8 * 3600,
                httponly=True,
                samesite="Lax",
            )
            return response
        error_message = "Invalid credentials."

    return render(request, "staff_login.html", {"error": error_message})


def moderation_queue(request: HttpRequest) -> HttpResponse:
    """Moderator queue listing with claim/decide actions."""
    from token_budget_platform.application.iam import AuthorizationService
    from token_budget_platform.application.moderation.service import ModerationService
    from token_budget_platform.domain.iam.policies import Resource

    identity = _staff_identity(request)
    if identity is None:
        return redirect("staff-login")
    decision = AuthorizationService().authorize(
        actor_username=identity["username"], resource=Resource.MODERATION_QUEUE
    )
    if not decision.allowed:
        return render(request, "manage_denied.html", status=403)

    service = ModerationService()
    message = None
    if request.method == "POST":
        message = _apply_moderation_post(request, identity, service)

    entries = service.queue()
    return render(
        request,
        "moderation_queue.html",
        {"entries": entries, "message": message, "username": identity["username"]},
    )


def _apply_moderation_post(request: HttpRequest, identity: dict, service) -> str:
    from token_budget_platform.adapters.django.models import StaffUser
    from token_budget_platform.application.moderation.service import (
        CaseNotFoundError,
        LeaseConflictError,
    )

    action = str(request.POST.get("action", ""))
    case_id = str(request.POST.get("case_id", ""))
    staff = StaffUser.objects.filter(username=identity["username"]).first()
    if staff is None:
        return "Staff account not available."

    try:
        case_uuid = UUID(case_id)
    except ValueError:
        return "Invalid case identifier."

    if action == "claim":
        try:
            service.claim(case_id=case_uuid, staff=staff)
            return "Case claimed; complete the decision before the lease expires."
        except (CaseNotFoundError, LeaseConflictError) as exc:
            return str(exc)

    if action == "decide":
        outcome = service.decide(
            case_id=case_uuid,
            staff=staff,
            action=str(request.POST.get("decision", "")),
            reason_code=str(request.POST.get("reason_code", "REVIEWED")),
            rationale=str(request.POST.get("rationale", "")),
            target_status_for_dispute=str(request.POST.get("target_status", "") or None),
        )
        return "Decision recorded." if outcome.success else f"Decision failed: {outcome.error}"
    return "Unknown action."


def administration(request: HttpRequest) -> HttpResponse:
    """Administrator console: staff provisioning, vocabulary, merges."""
    from token_budget_platform.adapters.django.models import ControlledValue
    from token_budget_platform.adapters.django.models import StaffUser as StaffUserModel
    from token_budget_platform.application.iam.authentication import AuthenticationService
    from token_budget_platform.application.iam.authorization import AuthorizationService
    from token_budget_platform.domain.iam.policies import Resource
    from token_budget_platform.domain.iam.staff import StaffRole

    identity = _staff_identity(request)
    if identity is None:
        return redirect("staff-login")
    decision = AuthorizationService().authorize(
        actor_username=identity["username"], resource=Resource.STAFF_PROVISIONING
    )
    if not decision.allowed:
        return render(request, "manage_denied.html", status=403)

    message = None
    provisioned = None
    if request.method == "POST":
        action = str(request.POST.get("action", ""))
        if action == "provision_staff":
            auth = AuthenticationService()
            try:
                provisioned = auth.provision_staff(
                    username=str(request.POST.get("new_username", "")),
                    password=str(request.POST.get("new_password", "")),
                    role=StaffRole(str(request.POST.get("new_role", "MODERATOR"))),
                )
                message = (
                    "Staff account provisioned. Store the recovery codes now; they are shown once."
                )
            except ValueError as exc:
                message = str(exc)

    return render(
        request,
        "administration.html",
        {
            "username": identity["username"],
            "message": message,
            "provisioned": provisioned,
            "staff": StaffUserModel.objects.order_by("username"),
            "controlled_values": ControlledValue.objects.order_by("kind", "label")[:100],
        },
    )


def _staff_identity(request: HttpRequest) -> dict | None:
    """Validate the signed staff cookie and the revocation epoch."""
    import django.core.signing as signing

    from token_budget_platform.adapters.django.models import StaffUser

    cookie = request.COOKIES.get(STAFF_SESSION_COOKIE, "")
    if not cookie:
        return None
    try:
        data = signing.loads(cookie, max_age=8 * 3600)
    except signing.BadSignature:
        return None
    user = StaffUser.objects.filter(username=str(data.get("username", "")), is_active=True).first()
    if user is None or user.session_revocation_epoch != int(data.get("epoch", -1)):
        return None
    return {"username": user.username, "role": user.role, "user_id": user.id}


# ──── Health endpoints ────────────────────────────────────────────────────────


def live(request: HttpRequest) -> JsonResponse:
    """Liveness: proves only that the process event loop responds."""
    from token_budget_platform.transports.api.views import _get_health_service

    service = _get_health_service()
    result = service.liveness()
    return JsonResponse({"status": result.status})


def ready(request: HttpRequest) -> JsonResponse:
    """Readiness: deployment version and coarse dependency status.

    Returns HTTP 200 for ok/degraded, HTTP 503 for unavailable.
    Never exposes endpoint names, credentials, content, counts, or exception text.
    """
    from token_budget_platform.application.health import ReadinessStatus
    from token_budget_platform.transports.api.views import _get_health_service

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


def _payload_from_post(request: HttpRequest) -> dict[str, object]:
    """Collect the submission form into the shared validation payload shape."""
    fields = (
        "organization_name",
        "industry",
        "size_band_id",
        "region_id",
        "model_provider_id",
        "ai_product_id",
        "workload_category_id",
        "budget_scope",
        "seat_count",
        "workload_count",
        "token_budget",
        "input_tokens",
        "output_tokens",
        "reporting_start",
        "reporting_end",
        "reporting_timezone",
        "source_date",
        "token_usage",
        "cost_minor_units",
        "cost_currency",
        "cost_basis",
        "methodology_notes",
        "contributor_attribution",
        "contact_email",
        "evidence_url",
    )
    payload: dict[str, object] = {}
    for field in fields:
        value = str(request.POST.get(field, "") or "").strip()
        if value:
            payload[field] = value
    return payload
