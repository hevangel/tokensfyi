"""Explicit role/resource authorization policies.

Requirement 7.13: Persons without Moderator/Administrator role denied;
authorization failure recorded.

Requirement 10.4: Authorize against assigned role and requested resource.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from token_budget_platform.domain.iam.staff import StaffRole


class Resource(StrEnum):
    """Protected resources requiring authorization."""

    MODERATION_QUEUE = "MODERATION_QUEUE"
    MODERATION_DECISION = "MODERATION_DECISION"
    EVIDENCE_ACCESS = "EVIDENCE_ACCESS"
    ORGANIZATION_MERGE = "ORGANIZATION_MERGE"
    CONTROLLED_VALUES = "CONTROLLED_VALUES"
    STAFF_PROVISIONING = "STAFF_PROVISIONING"
    AUDIT_VIEW = "AUDIT_VIEW"
    REPORT_RESOLUTION = "REPORT_RESOLUTION"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Outcome of a policy check."""

    allowed: bool
    actor_username: str
    actor_role: StaffRole | None
    resource: Resource
    reason: str = ""


# Explicit role-resource matrix.
# Administrators inherit all Moderator permissions plus exclusive admin resources.
_MODERATOR_RESOURCES: frozenset[Resource] = frozenset(
    {
        Resource.MODERATION_QUEUE,
        Resource.MODERATION_DECISION,
        Resource.EVIDENCE_ACCESS,
        Resource.ORGANIZATION_MERGE,
        Resource.REPORT_RESOLUTION,
        Resource.AUDIT_VIEW,
    }
)

_ADMINISTRATOR_RESOURCES: frozenset[Resource] = _MODERATOR_RESOURCES | frozenset(
    {
        Resource.CONTROLLED_VALUES,
        Resource.STAFF_PROVISIONING,
    }
)

_ROLE_PERMISSIONS: dict[StaffRole, frozenset[Resource]] = {
    StaffRole.MODERATOR: _MODERATOR_RESOURCES,
    StaffRole.ADMINISTRATOR: _ADMINISTRATOR_RESOURCES,
}


@dataclass(frozen=True, slots=True)
class RoleResourcePolicy:
    """Stateless policy evaluator for role/resource checks."""

    role_permissions: dict[StaffRole, frozenset[Resource]] = field(
        default_factory=lambda: dict(_ROLE_PERMISSIONS)
    )

    def check(
        self,
        *,
        actor_username: str,
        actor_role: StaffRole | None,
        resource: Resource,
    ) -> AuthorizationDecision:
        """Check if the actor's role grants access to the resource."""
        if actor_role is None:
            return AuthorizationDecision(
                allowed=False,
                actor_username=actor_username,
                actor_role=None,
                resource=resource,
                reason="no_role_assigned",
            )

        allowed_resources = self.role_permissions.get(actor_role, frozenset())
        if resource in allowed_resources:
            return AuthorizationDecision(
                allowed=True,
                actor_username=actor_username,
                actor_role=actor_role,
                resource=resource,
            )

        return AuthorizationDecision(
            allowed=False,
            actor_username=actor_username,
            actor_role=actor_role,
            resource=resource,
            reason=f"role_{actor_role.value}_lacks_{resource.value}",
        )


class AuthorizationPolicy:
    """High-level authorization facade combining identity and resource checks."""

    def __init__(self, policy: RoleResourcePolicy | None = None) -> None:
        self._policy = policy or RoleResourcePolicy()

    def authorize(
        self,
        *,
        actor_username: str,
        actor_role: StaffRole | None,
        resource: Resource,
    ) -> AuthorizationDecision:
        """Authorize and return the decision for event logging."""
        return self._policy.check(
            actor_username=actor_username,
            actor_role=actor_role,
            resource=resource,
        )
