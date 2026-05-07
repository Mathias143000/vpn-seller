from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.db.models import PaymentStatus


@dataclass(slots=True)
class PaymentCreateResult:
    payment_url: str
    provider_payment_id: str
    idempotency_key: str | None = None
    provider_metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedPaymentEvent:
    provider: str
    provider_event_id: str
    provider_payment_id: str
    event_type: str
    status: str
    amount_value: Decimal
    amount_currency: str
    order_id: int | None
    raw_payload: dict
    paid_at: datetime | None = None
    provider_metadata: dict = field(default_factory=dict)


class PaymentProvider(ABC):
    provider_name: str

    @abstractmethod
    async def create_payment(
        self,
        *,
        order_id: int,
        amount_value: Decimal,
        amount_currency: str,
        description: str,
        payment_url: str | None = None,
    ) -> PaymentCreateResult:
        raise NotImplementedError

    @abstractmethod
    async def parse_webhook(self, payload: dict) -> NormalizedPaymentEvent:
        raise NotImplementedError

    @abstractmethod
    async def get_payment_status(self, provider_payment_id: str) -> str:
        raise NotImplementedError


def normalize_payment_status(raw_status: str) -> str:
    mapping = {
        "pending": PaymentStatus.PENDING.value,
        "waiting_for_capture": PaymentStatus.PENDING.value,
        "succeeded": PaymentStatus.SUCCEEDED.value,
        "canceled": PaymentStatus.CANCELED.value,
        "failed": PaymentStatus.FAILED.value,
        "refunded": PaymentStatus.REFUNDED.value,
    }
    return mapping.get(raw_status, raw_status)
