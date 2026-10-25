"""Capturable local notification adapter (Mailpit-compatible at the port)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from token_budget_platform.domain.ports import DeliveryReference, Notification


@dataclass(frozen=True, slots=True)
class CapturedNotification:
    message: Notification
    delivery: DeliveryReference


class LocalNotificationAdapter:
    def __init__(self) -> None:
        self.messages: list[CapturedNotification] = []

    def send(self, message: Notification) -> DeliveryReference:
        delivery = DeliveryReference(provider="local-mailpit", message_id=str(uuid4()))
        self.messages.append(CapturedNotification(message=message, delivery=delivery))
        return delivery
