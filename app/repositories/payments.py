from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Payment, PaymentStatus
from app.repositories.base import BaseRepository


class PaymentsRepository(BaseRepository):
    async def get_by_provider_payment_id(self, *, provider: str, provider_payment_id: str) -> Payment | None:
        query = select(Payment).where(
            Payment.provider == provider,
            Payment.provider_payment_id == provider_payment_id,
        )
        return await self.session.scalar(query)

    async def upsert(
        self,
        *,
        order_id: int,
        provider: str,
        provider_payment_id: str,
        amount_value: Decimal,
        amount_currency: str,
        status: str = PaymentStatus.PENDING.value,
        raw_payload_json: dict | None = None,
        provider_metadata_json: dict | None = None,
        idempotency_key: str | None = None,
        paid_at: datetime | None = None,
    ) -> Payment:
        payment = await self.get_by_provider_payment_id(provider=provider, provider_payment_id=provider_payment_id)
        if payment is None:
            payment = Payment(
                order_id=order_id,
                provider=provider,
                provider_payment_id=provider_payment_id,
                amount_value=amount_value,
                amount_currency=amount_currency,
                status=status,
                raw_payload_json=raw_payload_json or {},
                provider_metadata_json=provider_metadata_json or {},
                idempotency_key=idempotency_key,
                paid_at=paid_at,
            )
            self.session.add(payment)
        else:
            payment.status = status
            payment.raw_payload_json = raw_payload_json or payment.raw_payload_json
            payment.provider_metadata_json = provider_metadata_json or payment.provider_metadata_json
            payment.idempotency_key = idempotency_key or payment.idempotency_key
            payment.paid_at = paid_at or payment.paid_at
        await self.session.flush()
        return payment

    async def get_latest_by_order_id(self, order_id: int) -> Payment | None:
        query = select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at.desc(), Payment.id.desc())
        return await self.session.scalar(query)
