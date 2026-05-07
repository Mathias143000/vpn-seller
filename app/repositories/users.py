from __future__ import annotations

from sqlalchemy import Select, or_, select

from app.db.models import Order, User, UserChannel, UserRole
from app.repositories.base import BaseRepository


class UsersRepository(BaseRepository):
    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> User | None:
        query: Select[tuple[User]] = select(User).where(User.telegram_user_id == telegram_user_id)
        return await self.session.scalar(query)

    async def get_by_vk_user_id(self, vk_user_id: int) -> User | None:
        query: Select[tuple[User]] = select(User).where(User.vk_user_id == vk_user_id)
        return await self.session.scalar(query)

    async def get_by_whatsapp_phone(self, whatsapp_phone: str) -> User | None:
        query: Select[tuple[User]] = select(User).where(User.whatsapp_phone == whatsapp_phone)
        return await self.session.scalar(query)

    async def get_by_username(self, username: str) -> User | None:
        normalized = username.lstrip("@")
        query: Select[tuple[User]] = select(User).where(User.username == normalized)
        return await self.session.scalar(query)

    async def get_by_contact_id(self, contact_id: int) -> User | None:
        query: Select[tuple[User]] = select(User).where(
            or_(
                User.telegram_user_id == contact_id,
                User.vk_user_id == contact_id,
                User.whatsapp_phone == str(contact_id),
            )
        )
        return await self.session.scalar(query)

    async def get_or_create(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        full_name: str | None,
        admin_ids: list[int],
    ) -> User:
        user = await self.get_by_telegram_user_id(telegram_user_id)
        if user is None:
            role = UserRole.SUPERADMIN.value if telegram_user_id in admin_ids else UserRole.USER.value
            user = User(
                telegram_user_id=telegram_user_id,
                username=username,
                full_name=full_name,
                role=role,
            )
            self.session.add(user)
            await self.session.flush()
            return user

        user.delivery_channel = UserChannel.TELEGRAM.value
        user.username = username
        user.full_name = full_name
        if telegram_user_id in admin_ids and user.role == UserRole.USER.value:
            user.role = UserRole.SUPERADMIN.value
        await self.session.flush()
        return user

    async def set_active_promo_code(self, *, user: User, promo_code: str | None) -> User:
        user.active_promo_code = promo_code
        await self.session.flush()
        return user

    async def get_or_create_vk(
        self,
        *,
        vk_user_id: int,
        username: str | None,
        full_name: str | None,
    ) -> User:
        user = await self.get_by_vk_user_id(vk_user_id)
        synthetic_telegram_user_id = self._synthetic_telegram_id_for_vk(vk_user_id)
        if user is None:
            user = User(
                telegram_user_id=synthetic_telegram_user_id,
                vk_user_id=vk_user_id,
                delivery_channel=UserChannel.VK.value,
                username=username,
                full_name=full_name,
                role=UserRole.USER.value,
            )
            self.session.add(user)
            await self.session.flush()
            return user

        user.delivery_channel = UserChannel.VK.value
        user.vk_user_id = vk_user_id
        user.username = username or user.username
        user.full_name = full_name or user.full_name
        if user.telegram_user_id >= 0:
            user.telegram_user_id = synthetic_telegram_user_id
        await self.session.flush()
        return user

    async def get_or_create_whatsapp(
        self,
        *,
        whatsapp_phone: str,
        username: str | None,
        full_name: str | None,
    ) -> User:
        user = await self.get_by_whatsapp_phone(whatsapp_phone)
        synthetic_telegram_user_id = self._synthetic_telegram_id_for_whatsapp(whatsapp_phone)
        if user is None:
            user = User(
                telegram_user_id=synthetic_telegram_user_id,
                whatsapp_phone=whatsapp_phone,
                delivery_channel=UserChannel.WHATSAPP.value,
                username=username,
                full_name=full_name,
                role=UserRole.USER.value,
            )
            self.session.add(user)
            await self.session.flush()
            return user

        user.delivery_channel = UserChannel.WHATSAPP.value
        user.whatsapp_phone = whatsapp_phone
        user.username = username or user.username
        user.full_name = full_name or user.full_name
        if user.telegram_user_id >= 0:
            user.telegram_user_id = synthetic_telegram_user_id
        await self.session.flush()
        return user

    async def list_admins(self) -> list[User]:
        query = select(User).where(User.role.in_([UserRole.SUPERADMIN.value, UserRole.ADMIN.value]))
        result = await self.session.scalars(query)
        return list(result)

    async def list_customers(self) -> list[User]:
        query = (
            select(User)
            .join(Order, Order.user_id == User.id)
            .where(User.is_blocked.is_(False))
            .distinct()
            .order_by(User.created_at.asc())
        )
        result = await self.session.scalars(query)
        return list(result)

    @staticmethod
    def _synthetic_telegram_id_for_vk(vk_user_id: int) -> int:
        return -abs(vk_user_id)

    @staticmethod
    def _synthetic_telegram_id_for_whatsapp(whatsapp_phone: str) -> int:
        normalized = "".join(ch for ch in whatsapp_phone if ch.isdigit())[-15:] or "0"
        return -(1_000_000_000_000_000 + int(normalized))
