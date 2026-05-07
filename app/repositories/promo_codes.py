from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.db.models import PromoCode, PromoRedemption
from app.repositories.base import BaseRepository


class PromoCodesRepository(BaseRepository):
    async def get_by_code(self, code: str) -> PromoCode | None:
        return await self.session.scalar(select(PromoCode).where(PromoCode.code == code))

    async def lock_by_code(self, code: str) -> PromoCode | None:
        return await self.session.scalar(select(PromoCode).where(PromoCode.code == code).with_for_update())

    async def list_active(self) -> list[PromoCode]:
        result = await self.session.scalars(select(PromoCode).order_by(PromoCode.created_at.desc()))
        return list(result)

    async def create_or_update(
        self,
        *,
        code: str,
        discount_type: str,
        discount_value: Decimal,
        max_uses: int | None,
        description: str | None = None,
        starts_at: datetime | None = None,
        expires_at: datetime | None = None,
        is_active: bool = True,
    ) -> PromoCode:
        promo = await self.get_by_code(code)
        if promo is None:
            promo = PromoCode(code=code)
            self.session.add(promo)
        promo.discount_type = discount_type
        promo.discount_value = discount_value
        promo.max_uses = max_uses
        promo.description = description
        promo.starts_at = starts_at
        promo.expires_at = expires_at
        promo.is_active = is_active
        await self.session.flush()
        return promo

    async def has_user_redemption(self, *, promo_code_id: int, user_id: int) -> bool:
        redemption = await self.session.scalar(
            select(PromoRedemption.id).where(
                PromoRedemption.promo_code_id == promo_code_id,
                PromoRedemption.user_id == user_id,
            )
        )
        return redemption is not None

    async def redeem(
        self,
        *,
        promo_code: PromoCode,
        user_id: int,
        order_id: int,
        discount_amount_value: Decimal,
    ) -> PromoRedemption:
        promo_code.used_count += 1
        redemption = PromoRedemption(
            promo_code_id=promo_code.id,
            user_id=user_id,
            order_id=order_id,
            discount_amount_value=discount_amount_value,
        )
        self.session.add(redemption)
        await self.session.flush()
        return redemption
