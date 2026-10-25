"""AWS client composition for the shared application image.

The same OCI image serves web, worker, and scanner roles on ECS Fargate.
Environment variables and Secrets Manager/SSM provide all runtime values
without source-code modification (Req 15.4).

Domain logic, validation, normalization, moderation-state, and privacy
decisions remain in shared application/domain code (Req 15.3).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .backup import SignedBackupGate
from .key_management import AwsKmsKeyManagement
from .notification import AwsSesNotification, AwsSnsNotification
from .object_storage import AwsS3ObjectStore
from .scanner import AwsClamAvScanner
from .telemetry import AwsTelemetry


@dataclass(frozen=True, slots=True)
class AwsRuntime:
    """Production adapter composition wired to AWS managed services."""

    object_store: AwsS3ObjectStore
    key_management: AwsKmsKeyManagement
    email: AwsSesNotification
    operator_notifications: AwsSnsNotification
    backup_gate: SignedBackupGate
    telemetry: AwsTelemetry
    scanner: AwsClamAvScanner
    database_config: dict[str, str] = field(default_factory=dict)
    service_role: str = "web"


# Required environment variables that cannot have safe defaults.
REQUIRED_SETTINGS = (
    "AWS_S3_BUCKET",
    "AWS_KMS_KEY_ID",
    "SES_FROM_ADDRESS",
    "OPS_SNS_TOPIC_ARN",
    "BACKUP_EVENT_SIGNING_KEY",
)


def build_aws_runtime(
    environ: Mapping[str, str] | None = None,
    *,
    clients: dict[str, Any] | None = None,
) -> AwsRuntime:
    """Compose production adapters from external configuration.

    All runtime values come from environment variables or AWS parameter store.
    Secrets Manager provides database credentials so the image remains immutable.
    """
    values = os.environ if environ is None else environ
    missing = [name for name in REQUIRED_SETTINGS if not values.get(name)]
    if missing:
        raise RuntimeError(f"Missing AWS runtime settings: {', '.join(missing)}")

    sdk = _build_sdk_clients(clients)

    # Resolve database credentials from Secrets Manager (Req 15.4).
    database_config = _resolve_database_config(values, sdk)

    scanner_host = values.get("SCANNER_HOST", "localhost")
    scanner_port = int(values.get("SCANNER_PORT", "3310"))

    return AwsRuntime(
        object_store=AwsS3ObjectStore(
            values["AWS_S3_BUCKET"],
            kms_key_id=values["AWS_KMS_KEY_ID"],
            client=sdk["s3"],
        ),
        key_management=AwsKmsKeyManagement(values["AWS_KMS_KEY_ID"], client=sdk["kms"]),
        email=AwsSesNotification(values["SES_FROM_ADDRESS"], client=sdk["ses"]),
        operator_notifications=AwsSnsNotification(values["OPS_SNS_TOPIC_ARN"], client=sdk["sns"]),
        backup_gate=SignedBackupGate(bytes.fromhex(values["BACKUP_EVENT_SIGNING_KEY"])),
        telemetry=AwsTelemetry(
            values.get("CLOUDWATCH_NAMESPACE", "TokenBudgetPlatform"),
            cloudwatch_client=sdk["cloudwatch"],
        ),
        scanner=AwsClamAvScanner(host=scanner_host, port=scanner_port),
        database_config=database_config,
        service_role=values.get("SERVICE_ROLE", "web"),
    )


def _build_sdk_clients(clients: dict[str, Any] | None) -> dict[str, Any]:
    """Lazily build boto3 clients only when not injected for testing."""
    if clients:
        return dict(clients)
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for AWS runtime adapters") from exc
    return {
        "s3": boto3.client("s3"),
        "kms": boto3.client("kms"),
        "ses": boto3.client("ses"),
        "sns": boto3.client("sns"),
        "cloudwatch": boto3.client("cloudwatch"),
        "secretsmanager": boto3.client("secretsmanager"),
    }


def _resolve_database_config(values: Mapping[str, str], sdk: dict[str, Any]) -> dict[str, str]:
    """Resolve RDS credentials from Secrets Manager or DATABASE_URL fallback.

    Secrets Manager provides JSON with username, password, host, port, dbname.
    This keeps credentials out of environment variables and supports rotation.
    """
    secret_arn = values.get("DATABASE_SECRET_ARN")
    if secret_arn and "secretsmanager" in sdk:
        try:
            response = sdk["secretsmanager"].get_secret_value(SecretId=secret_arn)
            secret = json.loads(response["SecretString"])
            return {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": secret.get("dbname", "tokenbudget"),
                "USER": secret.get("username", "tokenbudget"),
                "PASSWORD": secret.get("password", ""),
                "HOST": secret.get("host", "localhost"),
                "PORT": str(secret.get("port", "5432")),
                "CONN_MAX_AGE": "600",
                "OPTIONS": json.dumps({"sslmode": "require"}),
            }
        except Exception:
            # Fall through to DATABASE_URL if secret resolution fails during
            # startup in test environments.
            pass

    # Fallback: construct from DATABASE_URL if present.
    database_url = values.get("DATABASE_URL", "")
    if database_url:
        from urllib.parse import urlparse

        parsed = urlparse(database_url)
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
            "CONN_MAX_AGE": "600",
            "OPTIONS": json.dumps({"sslmode": "require"}),
        }

    return {}
