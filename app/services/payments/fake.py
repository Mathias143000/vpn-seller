from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.db.models import PaymentStatus
from app.services.payments.base import NormalizedPaymentEvent, PaymentCreateResult, PaymentProvider, normalize_payment_status


class FakePaymentProvider(PaymentProvider):
    provider_name = "fake"

    async def create_payment(
        self,
        *,
        order_id: int,
        amount_value: Decimal,
        amount_currency: str,
        description: str,
        payment_url: str | None = None,
    ) -> PaymentCreateResult:
        provider_payment_id = f"fake-{uuid4().hex}"
        payment_url = f"/fake/payments/{provider_payment_id}?order_id={order_id}"
        return PaymentCreateResult(
            payment_url=payment_url,
            provider_payment_id=provider_payment_id,
            idempotency_key=f"fake-{uuid4().hex}",
            provider_metadata={"description": description},
        )

    async def parse_webhook(self, payload: dict) -> NormalizedPaymentEvent:
        provider_payment_id = payload["provider_payment_id"]
        event_type = payload.get("event_type", "payment.succeeded")
        raw_status = payload.get("status", PaymentStatus.SUCCEEDED.value)
        paid_at_raw = payload.get("paid_at")
        paid_at = datetime.fromisoformat(paid_at_raw) if paid_at_raw else datetime.now(tz=timezone.utc)
        return NormalizedPaymentEvent(
            provider=self.provider_name,
            provider_event_id=payload.get("provider_event_id", f"{event_type}:{provider_payment_id}:{raw_status}"),
            provider_payment_id=provider_payment_id,
            event_type=event_type,
            status=normalize_payment_status(raw_status),
            amount_value=Decimal(str(payload["amount_value"])),
            amount_currency=payload.get("amount_currency", "RUB"),
            order_id=payload.get("order_id"),
            raw_payload=payload,
            paid_at=paid_at,
            provider_metadata=payload.get("metadata", {}),
        )

    async def get_payment_status(self, provider_payment_id: str) -> str:
        return PaymentStatus.PENDING.value
