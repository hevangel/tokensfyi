"""Infrastructure configuration and rendered-template security checks."""

from __future__ import annotations

from typing import Any

import pytest
from infrastructure.config import EnvironmentConfig, InfrastructureConfigurationError
from infrastructure.policy import validate_synthesized_template

_VALID: dict[str, Any] = {
    "environment": "production",
    "account": "123456789012",
    "region": "us-east-1",
    "identifier_prefix": "token-budget-production",
}


@pytest.mark.parametrize(
    "unsafe_setting",
    [
        "public_rds",
        "public_s3",
        "evidence_public_path",
        "personal_data_public_path",
        "wildcard_data_plane_access",
        "encryption_enabled",
    ],
)
def test_environment_rejects_unsafe_data_plane_configuration(unsafe_setting: str) -> None:
    values = dict(_VALID)
    values[unsafe_setting] = False if unsafe_setting == "encryption_enabled" else True

    with pytest.raises(InfrastructureConfigurationError):
        EnvironmentConfig(**values).validate()


def test_production_rejects_shared_identifier() -> None:
    with pytest.raises(InfrastructureConfigurationError, match="shared"):
        EnvironmentConfig(
            **{**_VALID, "identifier_prefix": "shared-platform-production"}
        ).validate()


def test_policy_rejects_public_rds_and_unencrypted_s3() -> None:
    config = EnvironmentConfig(**_VALID)
    template = {
        "Resources": {
            "Database": {
                "Type": "AWS::RDS::DBInstance",
                "Properties": {"PubliclyAccessible": True, "StorageEncrypted": False},
            },
            "Bucket": {
                "Type": "AWS::S3::Bucket",
                "Properties": {
                    "BucketName": "token-budget-production-private-data",
                    "VersioningConfiguration": {"Status": "Enabled"},
                    "PublicAccessBlockConfiguration": {
                        "BlockPublicAcls": True,
                        "BlockPublicPolicy": True,
                        "IgnorePublicAcls": True,
                        "RestrictPublicBuckets": True,
                    },
                },
            },
        }
    }

    with pytest.raises(InfrastructureConfigurationError):
        validate_synthesized_template(template, config)


def test_production_stack_synthesizes_private_encrypted_resources() -> None:
    from aws_cdk import App
    from infrastructure.policy import validate_synthesized_template
    from infrastructure.stacks import TokenBudgetPlatformStack

    config = EnvironmentConfig(**_VALID)
    app = App()
    stack = TokenBudgetPlatformStack(app, "TokenBudgetPlatformProduction", config=config)
    template = app.synth().get_stack_by_name(stack.artifact_id).template

    validate_synthesized_template(template, config)
    rds_resources = [
        resource
        for resource in template["Resources"].values()
        if resource.get("Type") == "AWS::RDS::DBInstance"
    ]
    bucket_resources = [
        resource
        for resource in template["Resources"].values()
        if resource.get("Type") == "AWS::S3::Bucket"
    ]
    assert len(rds_resources) == 1
    assert len(bucket_resources) == 1
    assert rds_resources[0]["Properties"]["PubliclyAccessible"] is False
    assert rds_resources[0]["Properties"]["StorageEncrypted"] is True
    assert bucket_resources[0]["Properties"]["VersioningConfiguration"]["Status"] == "Enabled"
    assert bucket_resources[0]["Properties"]["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
