"""SES and SNS notification adapters."""

from __future__ import annotations

from typing import Any

from token_budget_platform.domain.ports import DeliveryReference, Notification


class AwsSesNotification:
    def __init__(self, sender: str, *, client: Any) -> None:
        if not sender:
            raise ValueError("SES sender is required")
        self.sender = sender
        self.client = client

    def send(self, message: Notification) -> DeliveryReference:
        response = self.client.send_email(
            Source=self.sender,
            Destination={"ToAddresses": [message.destination]},
            Message={
                "Subject": {"Data": message.template, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": _body(message), "Charset": "UTF-8"}},
            },
        )
        return DeliveryReference(provider="aws-ses", message_id=str(response["MessageId"]))


class AwsSnsNotification:
    def __init__(self, topic_arn: str, *, client: Any) -> None:
        if not topic_arn:
            raise ValueError("SNS operator topic ARN is required")
        self.topic_arn = topic_arn
        self.client = client

    def send(self, message: Notification) -> DeliveryReference:
        response = self.client.publish(
            TopicArn=self.topic_arn,
            Subject=message.template[:100],
            Message=_body(message),
            MessageAttributes={"channel": {"DataType": "String", "StringValue": message.channel}},
        )
        return DeliveryReference(provider="aws-sns", message_id=str(response["MessageId"]))


def _body(message: Notification) -> str:
    values = ", ".join(f"{key}={value}" for key, value in sorted(message.parameters.items()))
    reference = f" reference={message.reference_id}" if message.reference_id else ""
    return f"{message.template}{reference}" + (f" ({values})" if values else "")
