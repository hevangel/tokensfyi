"""AWS production adapters and runtime composition.

AWS SDK imports are intentionally lazy: local development and domain tests can run
without AWS credentials or SDK initialization.
"""

from .backup import InvalidBackupEvent, SignedBackupGate
from .key_management import AwsKmsKeyManagement
from .notification import AwsSesNotification, AwsSnsNotification
from .object_storage import AwsS3ObjectStore
from .runtime import AwsRuntime, build_aws_runtime
from .telemetry import AwsTelemetry

__all__ = [
    "AwsKmsKeyManagement",
    "AwsRuntime",
    "AwsS3ObjectStore",
    "AwsSesNotification",
    "AwsSnsNotification",
    "AwsTelemetry",
    "InvalidBackupEvent",
    "SignedBackupGate",
    "build_aws_runtime",
]
