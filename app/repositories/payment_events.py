from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.db.models import PaymentEvent, ProcessingStatus
from app.repositories.base import BaseRepository


class PaymentEventsRepository(BaseRepository):
    async def record_event(
        self,
        *,
        provider: str,
        provider_event_id: str,
        provider_payment_id: str,
        event_type: str,
        raw_payload_json: dict,
    ) -> tuple[PaymentEvent | None, bool]:
        existing = await self.session.scalar(
            select(PaymentEvent).where(
                PaymentEvent.provider == provider,
                PaymentEvent.provider_event_id == provider_event_id,
            )
        )
        if existing is not None:
            return existing, False
        payment_event = PaymentEvent(
            provider=provider,
            provider_event_id=provider_event_id,
            provider_payment_id=provider_payment_id,
            event_type=event_type,
            raw_payload_json=raw_payload_json,
        )
        self.session.add(payment_event)
        await self.session.flush()
        return payment_event, True

    async def mark_processed(self, payment_event: PaymentEvent, *, status: str, processed_at: datetime) -> PaymentEvent:
        payment_event.processing_status = status
        payment_event.processed_at = processed_at
        await self.session.flush()
        return payment_event

    async def mark_duplicate(self, provider: str, provider_event_id: str) -> PaymentEvent | None:
        payment_event = await self.session.scalar(
            select(PaymentEvent).where(
                PaymentEvent.provider == provider,
                PaymentEvent.provider_event_id == provider_event_id,
            )
        )
        if payment_event is None:
            return None
        payment_event.processing_status = ProcessingStatus.DUPLICATE.value
        await self.session.flush()
        return payment_event
