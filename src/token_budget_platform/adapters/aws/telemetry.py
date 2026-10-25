"""CloudWatch metrics and optional X-Ray trace adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class AwsTelemetry:
    def __init__(
        self, namespace: str, *, cloudwatch_client: Any, xray_recorder: Any | None = None
    ) -> None:
        if not namespace:
            raise ValueError("CloudWatch namespace is required")
        self.namespace = namespace
        self.cloudwatch = cloudwatch_client
        self.xray = xray_recorder

    def emit(self, event_name: str, *, attributes: dict[str, Any] | None = None) -> None:
        if self.xray is not None:
            self.xray.put_annotation("event", event_name)
            for key, value in (attributes or {}).items():
                if isinstance(value, (str, int, float, bool)):
                    self.xray.put_annotation(key[:250], value)

    def increment(
        self, metric_name: str, *, value: int = 1, attributes: dict[str, str] | None = None
    ) -> None:
        dimensions = [
            {"Name": key[:255], "Value": value[:255]}
            for key, value in sorted((attributes or {}).items())
            if value
        ][:10]
        self.cloudwatch.put_metric_data(
            Namespace=self.namespace,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "Count",
                    "Timestamp": datetime.now(UTC),
                    **({"Dimensions": dimensions} if dimensions else {}),
                }
            ],
        )
