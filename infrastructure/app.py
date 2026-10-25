"""CDK entry point with pre-deployment policy validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aws_cdk import App

from .config import EnvironmentConfig
from .policy import validate_synthesized_template
from .stacks import TokenBudgetPlatformStack


def synthesize_and_validate(context: Mapping[str, Any] | None = None) -> Any:
    """Synthesize exactly one environment and fail before deployment on policy violations."""

    app = App(context=dict(context or {}))
    raw_context = dict(context or {})
    nested_context = raw_context.get("environment_config")
    config_context = (
        dict(nested_context)
        if isinstance(nested_context, Mapping)
        else {
            key: app.node.try_get_context(key)
            for key in (
                "environment",
                "account",
                "region",
                "identifier_prefix",
                "public_rds",
                "public_s3",
                "evidence_public_path",
                "personal_data_public_path",
                "wildcard_data_plane_access",
                "encryption_enabled",
                "tls_min_version",
                "runtime_parameter_value",
            )
            if app.node.try_get_context(key) is not None
        }
    )
    config = EnvironmentConfig.from_context(config_context)
    stack = TokenBudgetPlatformStack(
        app,
        f"TokenBudgetPlatform-{config.environment.title()}",
        config=config,
        env={"account": config.account, "region": config.region},
    )
    assembly = app.synth()
    artifact = assembly.get_stack_by_name(stack.artifact_id)
    validate_synthesized_template(artifact.template, config)
    return assembly


def main() -> None:
    synthesize_and_validate()


if __name__ == "__main__":
    main()
