from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.config import Settings
from app.db.models import PaymentStatus
from app.services.payments.base import NormalizedPaymentEvent, PaymentCreateResult, PaymentProvider


class DonateStreamPaymentProvider(PaymentProvider):
    provider_name = "donate_stream"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def create_payment(
        self,
        *,
        order_id: int,
        amount_value: Decimal,
        amount_currency: str,
        description: str,
        payment_url: str | None = None,
    ) -> PaymentCreateResult:
        provider_payment_id = f"donatestream-{uuid4().hex}"
        effective_payment_url = (payment_url or self._settings.donate_stream_url).strip()
        if not effective_payment_url:
            raise RuntimeError("DONATE_STREAM_URL is not configured")
        return PaymentCreateResult(
            payment_url=effective_payment_url,
            provider_payment_id=provider_payment_id,
            provider_metadata={
                "description": description,
                "mode": "manual_review",
                "order_reference": order_id,
            },
        )

    async def parse_webhook(self, payload: dict) -> NormalizedPaymentEvent:
        raise RuntimeError("DonateStream manual mode does not support webhook parsing in this project")

    async def get_payment_status(self, provider_payment_id: str) -> str:
        return PaymentStatus.PENDING.value
