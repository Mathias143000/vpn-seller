from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select

from app.db.models import Order, OrderStatus, User
from app.repositories.base import BaseRepository


class OrdersRepository(BaseRepository):
    async def create(
        self,
        *,
        user_id: int,
        plan_id: int,
        amount_value: Decimal,
        amount_currency: str,
        payment_provider: str,
        fulfillment_mode: str,
        preferred_hiddify_server_id: int | None = None,
        original_amount_value: Decimal | None = None,
        discount_amount_value: Decimal | None = None,
        promo_code_id: int | None = None,
        promo_code: str | None = None,
    ) -> Order:
        order = Order(
            user_id=user_id,
            plan_id=plan_id,
            amount_value=amount_value,
            amount_currency=amount_currency,
            original_amount_value=original_amount_value,
            discount_amount_value=discount_amount_value or Decimal("0.00"),
            promo_code_id=promo_code_id,
            promo_code=promo_code,
            payment_provider=payment_provider,
            fulfillment_mode=fulfillment_mode,
            preferred_hiddify_server_id=preferred_hiddify_server_id,
            status=OrderStatus.CREATED.value,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_by_id(self, order_id: int) -> Order | None:
        return await self.session.get(Order, order_id)

    async def lock_by_id(self, order_id: int) -> Order | None:
        query = select(Order).where(Order.id == order_id).with_for_update()
        return await self.session.scalar(query)

    async def get_by_provider_payment_id(self, provider_payment_id: str) -> Order | None:
        query = select(Order).where(Order.provider_payment_id == provider_payment_id)
        return await self.session.scalar(query)

    async def list_by_user(self, user_id: int) -> list[Order]:
        result = await self.session.scalars(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()))
        return list(result)

    async def search(self, query_text: str) -> list[Order]:
        query = select(Order).join(User, User.id == Order.user_id)
        filters = [Order.provider_payment_id == query_text, User.username == query_text.lstrip("@")]
        if query_text.isdigit():
            filters.extend(
                [
                    Order.id == int(query_text),
                    User.telegram_user_id == int(query_text),
                    User.vk_user_id == int(query_text),
                    User.whatsapp_phone == query_text,
                ]
            )
        else:
            normalized_phone = "".join(ch for ch in query_text if ch.isdigit())
            if normalized_phone:
                filters.append(User.whatsapp_phone == normalized_phone)
        result = await self.session.scalars(query.where(or_(*filters)).order_by(Order.created_at.desc()))
        return list(result)

    async def list_expired_reservations(self, now: datetime) -> list[Order]:
        query = select(Order).where(
            Order.reservation_expires_at.is_not(None),
            Order.reservation_expires_at < now,
            Order.status.in_([OrderStatus.CREATED.value, OrderStatus.PENDING_PAYMENT.value]),
        )
        result = await self.session.scalars(query)
        return list(result)

    async def list_pending_for_reconciliation(self, *, now: datetime, older_than_minutes: int) -> list[Order]:
        cutoff = now - timedelta(minutes=older_than_minutes)
        query = select(Order).where(
            Order.status == OrderStatus.PENDING_PAYMENT.value,
            Order.created_at < cutoff,
            Order.provider_payment_id.is_not(None),
        )
        result = await self.session.scalars(query)
        return list(result)

    async def list_paid_but_not_issued(self) -> list[Order]:
        result = await self.session.scalars(
            select(Order).where(Order.status == OrderStatus.PAID_BUT_NOT_ISSUED.value).order_by(Order.created_at.asc())
        )
        return list(result)
