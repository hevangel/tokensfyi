"""Fail-closed checks for synthesized CloudFormation templates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .config import EnvironmentConfig, InfrastructureConfigurationError

_DATA_PLANE_PREFIXES = (
    "s3:",
    "kms:",
    "secretsmanager:",
    "ssm:",
    "rds:",
)
_NAMED_RESOURCE_PROPERTIES = (
    "BucketName",
    "DBInstanceIdentifier",
    "SecretName",
    "AliasName",
    "ParameterName",
    "RoleName",
    "BackupVaultName",
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _literal(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _is_wildcard_resource(value: Any) -> bool:
    return value == "*" or isinstance(value, list) and "*" in value


def _has_kms_encryption(properties: Mapping[str, Any]) -> bool:
    configurations = properties.get("BucketEncryption", {}).get(
        "ServerSideEncryptionConfiguration", []
    )
    return any(
        isinstance(item, Mapping)
        and item.get("ServerSideEncryptionByDefault", {}).get("SSEAlgorithm") == "aws:kms"
        and item.get("ServerSideEncryptionByDefault", {}).get("KMSMasterKeyID")
        for item in configurations
    )


def validate_synthesized_template(template: Mapping[str, Any], config: EnvironmentConfig) -> None:
    """Validate security invariants on a rendered CloudFormation template.

    This is deliberately independent of CDK assertions so CI and a deployment wrapper
    can run it against either a CDK template object or a parsed template JSON document.
    """

    config.validate()
    resources = template.get("Resources", {})
    errors: list[str] = []
    environment = config.environment.lower()

    for logical_id, resource in resources.items():
        if not isinstance(resource, Mapping):
            continue
        resource_type = resource.get("Type")
        properties = resource.get("Properties", {})
        if not isinstance(properties, Mapping):
            continue
        if resource_type == "AWS::RDS::DBInstance":
            if properties.get("PubliclyAccessible") is not False:
                errors.append(f"{logical_id}: RDS must not be publicly accessible")
            if properties.get("StorageEncrypted") is not True or not properties.get("KmsKeyId"):
                errors.append(f"{logical_id}: RDS storage must use an owner-controlled KMS key")
        if resource_type == "AWS::S3::Bucket":
            public_block = properties.get("PublicAccessBlockConfiguration", {})
            required = (
                "BlockPublicAcls",
                "BlockPublicPolicy",
                "IgnorePublicAcls",
                "RestrictPublicBuckets",
            )
            if any(public_block.get(name) is not True for name in required):
                errors.append(f"{logical_id}: S3 public access block must be complete")
            if properties.get("VersioningConfiguration", {}).get("Status") != "Enabled":
                errors.append(f"{logical_id}: evidence storage must be versioned")
            if not _has_kms_encryption(properties):
                errors.append(f"{logical_id}: evidence storage must use KMS encryption")
            if properties.get("WebsiteConfiguration") or properties.get("AccessControl") in {
                "PublicRead",
                "PublicReadWrite",
            }:
                errors.append(f"{logical_id}: S3 cannot expose a public website or ACL")
        if resource_type == "AWS::IAM::Policy":
            document = properties.get("PolicyDocument", {})
            for statement in _as_list(document.get("Statement", [])):
                if not isinstance(statement, Mapping) or statement.get("Effect") != "Allow":
                    continue
                actions = [str(action).lower() for action in _as_list(statement.get("Action", []))]
                if any(
                    action == "*"
                    or action.startswith(prefix)
                    and _is_wildcard_resource(statement.get("Resource"))
                    for action in actions
                    for prefix in ("", *_DATA_PLANE_PREFIXES)
                ):
                    errors.append(f"{logical_id}: wildcard data-plane access is prohibited")
        if resource_type == "AWS::S3::BucketPolicy":
            document = properties.get("PolicyDocument", {})
            for statement in _as_list(document.get("Statement", [])):
                if not isinstance(statement, Mapping) or statement.get("Effect") != "Allow":
                    continue
                principal = statement.get("Principal")
                if principal == "*" or isinstance(principal, Mapping) and "*" in principal.values():
                    errors.append(f"{logical_id}: unauthenticated public S3 access is prohibited")
        for property_name in _NAMED_RESOURCE_PROPERTIES:
            value = _literal(properties.get(property_name))
            if value and environment not in value.lower():
                errors.append(
                    f"{logical_id}: {property_name} must include the environment identifier"
                )

    if errors:
        raise InfrastructureConfigurationError("; ".join(errors))
