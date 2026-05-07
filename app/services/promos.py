from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PromoCode, PromoDiscountType
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.promo_codes import PromoCodesRepository
from app.repositories.users import UsersRepository
from app.services.exceptions import InvalidStateError, NotFoundError
from app.services.pricing import quantize_money
from app.services.transactions import transactional


@dataclass(frozen=True)
class PromoPreview:
    code: str
    original_amount: Decimal
    discount_amount: Decimal
    final_amount: Decimal
    discount_label: str


class PromoService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        promo_codes_repo: PromoCodesRepository,
        users_repo: UsersRepository,
        audit_logs_repo: AuditLogsRepository,
        min_order_amount: int,
    ) -> None:
        self._session = session
        self._promo_codes_repo = promo_codes_repo
        self._users_repo = users_repo
        self._audit_logs_repo = audit_logs_repo
        self._min_order_amount = Decimal(str(min_order_amount))

    async def create_or_update(
        self,
        *,
        code: str,
        discount_type: str,
        discount_value: Decimal,
        max_uses: int | None,
        actor_user_id: int | None,
        description: str | None = None,
    ) -> PromoCode:
        normalized_code = self.normalize_code(code)
        if discount_type not in {PromoDiscountType.PERCENT.value, PromoDiscountType.FIXED.value}:
            raise InvalidStateError("Discount type must be percent or fixed")
        if discount_value <= 0:
            raise InvalidStateError("Discount value must be positive")
        if discount_type == PromoDiscountType.PERCENT.value and discount_value > 95:
            raise InvalidStateError("Percent discount cannot be higher than 95")

        async with transactional(self._session):
            promo = await self._promo_codes_repo.create_or_update(
                code=normalized_code,
                discount_type=discount_type,
                discount_value=discount_value,
                max_uses=max_uses,
                description=description,
            )
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="promo_code",
                entity_id=str(promo.id),
                action="promo_code_upserted",
                payload_json={
                    "code": promo.code,
                    "discount_type": promo.discount_type,
                    "discount_value": str(promo.discount_value),
                    "max_uses": promo.max_uses,
                },
            )
            return promo

    async def list_codes(self) -> list[PromoCode]:
        return await self._promo_codes_repo.list_active()

    async def set_active_for_user(self, *, user_id: int, code: str) -> PromoPreview:
        user = await self._users_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        promo = await self._promo_codes_repo.get_by_code(self.normalize_code(code))
        if promo is None:
            raise NotFoundError("Promo code not found")
        self._ensure_promo_available(promo)
        if await self._promo_codes_repo.has_user_redemption(promo_code_id=promo.id, user_id=user_id):
            raise InvalidStateError("Promo code has already been used by this user")
        async with transactional(self._session):
            await self._users_repo.set_active_promo_code(user=user, promo_code=promo.code)
        return self.preview(amount=Decimal("100.00"), promo=promo)

    async def clear_active_for_user(self, *, user_id: int) -> None:
        user = await self._users_repo.get_by_id(user_id)
        if user is not None:
            async with transactional(self._session):
                await self._users_repo.set_active_promo_code(user=user, promo_code=None)

    async def get_valid_promo(self, *, code: str, user_id: int) -> PromoCode:
        promo = await self._promo_codes_repo.lock_by_code(self.normalize_code(code))
        if promo is None:
            raise NotFoundError("Promo code not found")
        self._ensure_promo_available(promo)
        if await self._promo_codes_repo.has_user_redemption(promo_code_id=promo.id, user_id=user_id):
            raise InvalidStateError("Promo code has already been used by this user")
        return promo

    def preview(self, *, amount: Decimal, promo: PromoCode) -> PromoPreview:
        discount = self.calculate_discount(amount=amount, promo=promo)
        final_amount = quantize_money(max(amount - discount, self._min_order_amount))
        return PromoPreview(
            code=promo.code,
            original_amount=quantize_money(amount),
            discount_amount=discount,
            final_amount=final_amount,
            discount_label=self.discount_label(promo),
        )

    async def redeem_for_order(
        self,
        *,
        promo: PromoCode,
        user_id: int,
        order_id: int,
        original_amount: Decimal,
    ) -> PromoPreview:
        preview = self.preview(amount=original_amount, promo=promo)
        await self._promo_codes_repo.redeem(
            promo_code=promo,
            user_id=user_id,
            order_id=order_id,
            discount_amount_value=preview.discount_amount,
        )
        return preview

    def calculate_discount(self, *, amount: Decimal, promo: PromoCode) -> Decimal:
        amount = quantize_money(amount)
        if promo.discount_type == PromoDiscountType.PERCENT.value:
            return quantize_money(amount * Decimal(str(promo.discount_value)) / Decimal("100"))
        return quantize_money(min(Decimal(str(promo.discount_value)), amount - self._min_order_amount))

    @staticmethod
    def discount_label(promo: PromoCode) -> str:
        if promo.discount_type == PromoDiscountType.PERCENT.value:
            return f"{promo.discount_value}%"
        return f"{promo.discount_value} RUB"

    @staticmethod
    def normalize_code(code: str) -> str:
        return "".join(ch for ch in code.strip().upper() if ch.isalnum() or ch in {"-", "_"})[:64]

    @staticmethod
    def _ensure_promo_available(promo: PromoCode) -> None:
        now = datetime.now(tz=timezone.utc)
        if not promo.is_active:
            raise InvalidStateError("Promo code is inactive")
        if promo.starts_at and promo.starts_at > now:
            raise InvalidStateError("Promo code is not active yet")
        if promo.expires_at and promo.expires_at < now:
            raise InvalidStateError("Promo code expired")
        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            raise InvalidStateError("Promo code usage limit reached")
