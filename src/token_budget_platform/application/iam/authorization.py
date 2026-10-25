"""Authorization application service.

Requirements: 7.13, 10.4
- Every privileged request is checked against the explicit role/resource
  policy before any domain mutation.
- Denied requests change no domain state and emit a sanitized security
  event for the authorization failure.
"""

from __future__ import annotations

from uuid import UUID

from django.utils import timezone

from token_budget_platform.adapters.django.models import SecurityEvent, StaffUser
from token_budget_platform.domain.iam.policies import (
    AuthorizationDecision,
    AuthorizationPolicy,
    Resource,
)
from token_budget_platform.domain.iam.staff import StaffRole


class AuthorizationService:
    """Gate privileged operations behind role/resource policies."""

    def __init__(self, *, policy: AuthorizationPolicy | None = None) -> None:
        self._policy = policy or AuthorizationPolicy()

    def authorize(
        self,
        *,
        actor_username: str,
        resource: Resource,
        correlation_id: UUID | None = None,
    ) -> AuthorizationDecision:
        """Authorize by username, loading the current role from persistence."""
        role: StaffRole | None = None
        user = StaffUser.objects.filter(username=actor_username, is_active=True).first()
        if user is not None:
            role = StaffRole(user.role)
        decision = self._policy.authorize(
            actor_username=actor_username,
            actor_role=role,
            resource=resource,
        )
        if not decision.allowed:
            self._record_denial(decision, correlation_id)
        return decision

    @staticmethod
    def _record_denial(decision: AuthorizationDecision, correlation_id: UUID | None) -> None:
        """Record the denial without leaking resource internals or actor identity."""
        pseudonym = f"user:{decision.actor_username[:1]}*{len(decision.actor_username)}"
        try:
            SecurityEvent.objects.create(
                event_type="AUTHORIZATION_DENIED",
                occurred_at=timezone.now(),
                correlation_id=correlation_id or UUID(int=0),
                actor_or_network_pseudonym=pseudonym,
                target_surrogate=None,
                outcome="DENIED",
                category=f"AUTHORIZATION_{decision.resource.value}",
                sanitized_metadata={"reason": decision.reason},
            )
        except Exception:
            return
