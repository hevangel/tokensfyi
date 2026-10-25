"""Validated, environment-specific CDK configuration.

Validation runs before any construct is created.  The defaults intentionally fail
closed for data-plane exposure and require every identifier to carry its environment.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class InfrastructureConfigurationError(ValueError):
    """Raised when a deployment could violate an infrastructure invariant."""


_ENVIRONMENT_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_ALLOWED_ENVIRONMENTS = frozenset({"development", "staging", "production"})


def _context_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "1", "yes"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "0", "no"}:
        return False
    raise InfrastructureConfigurationError(f"boolean context value is invalid: {value!r}")


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    """Inputs needed to synthesize one isolated environment."""

    environment: str
    account: str | None = None
    region: str | None = None
    identifier_prefix: str | None = None
    public_rds: bool = False
    public_s3: bool = False
    evidence_public_path: bool = False
    personal_data_public_path: bool = False
    wildcard_data_plane_access: bool = False
    encryption_enabled: bool = True
    tls_min_version: str = "1.2"
    runtime_parameter_value: str = "managed-by-deployment"

    @property
    def prefix(self) -> str:
        return self.identifier_prefix or f"token-budget-{self.environment}"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> EnvironmentConfig:
        """Build configuration from CDK context without accepting unsafe defaults."""

        environment = str(context.get("environment", "development"))
        config = cls(
            environment=environment,
            account=str(context["account"]) if context.get("account") else None,
            region=str(context["region"]) if context.get("region") else None,
            identifier_prefix=(
                str(context["identifier_prefix"]) if context.get("identifier_prefix") else None
            ),
            public_rds=_context_bool(context.get("public_rds"), False),
            public_s3=_context_bool(context.get("public_s3"), False),
            evidence_public_path=_context_bool(context.get("evidence_public_path"), False),
            personal_data_public_path=_context_bool(
                context.get("personal_data_public_path"),
                False,
            ),
            wildcard_data_plane_access=_context_bool(
                context.get("wildcard_data_plane_access"),
                False,
            ),
            encryption_enabled=_context_bool(context.get("encryption_enabled"), True),
            tls_min_version=str(context.get("tls_min_version", "1.2")),
            runtime_parameter_value=str(
                context.get("runtime_parameter_value", "managed-by-deployment")
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Reject unsafe settings before a VPC or data resource is constructed."""

        errors: list[str] = []
        if self.environment not in _ALLOWED_ENVIRONMENTS or not _ENVIRONMENT_RE.fullmatch(
            self.environment
        ):
            errors.append("environment must be development, staging, or production")
        if not _IDENTIFIER_RE.fullmatch(self.prefix):
            errors.append(
                "identifier_prefix must contain only lowercase letters, digits, and hyphens"
            )
        if f"-{self.environment}" not in self.prefix:
            errors.append("identifier_prefix must include the environment name")
        if self.is_production and any(
            marker in self.prefix for marker in ("shared", "common", "default")
        ):
            errors.append("production identifiers cannot use shared/common/default names")
        if self.public_rds:
            errors.append("public RDS is prohibited")
        if self.public_s3:
            errors.append("public S3 is prohibited")
        if self.evidence_public_path:
            errors.append("Evidence cannot have an unauthenticated public path")
        if self.personal_data_public_path:
            errors.append("Personal_Data cannot have an unauthenticated public path")
        if self.wildcard_data_plane_access:
            errors.append("wildcard data-plane permissions are prohibited")
        if not self.encryption_enabled:
            errors.append("owner-controlled encryption is required")
        try:
            if float(self.tls_min_version) < 1.2:
                errors.append("TLS 1.2 or later is required")
        except ValueError:
            errors.append("tls_min_version must be a numeric TLS version")
        if self.account is not None and not re.fullmatch(r"\d{12}", self.account):
            errors.append("account must be a 12-digit AWS account ID")
        if not self.region:
            errors.append("region is required for AWS synthesis")
        if not self.runtime_parameter_value:
            errors.append("runtime_parameter_value cannot be empty")
        if errors:
            raise InfrastructureConfigurationError("; ".join(errors))
