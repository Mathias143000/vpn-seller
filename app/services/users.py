from __future__ import annotations

from aiogram.types import User as TelegramUser

from app.db.models import UserRole
from app.repositories.users import UsersRepository
from app.services.exceptions import AccessDeniedError


class UsersService:
    def __init__(self, users_repo: UsersRepository, admin_ids: list[int]) -> None:
        self._users_repo = users_repo
        self._admin_ids = admin_ids

    async def ensure_from_telegram(self, telegram_user: TelegramUser):
        return await self._users_repo.get_or_create(
            telegram_user_id=telegram_user.id,
            username=telegram_user.username,
            full_name=telegram_user.full_name,
            admin_ids=self._admin_ids,
        )

    async def ensure_from_vk(
        self,
        *,
        vk_user_id: int,
        username: str | None,
        full_name: str | None,
    ):
        return await self._users_repo.get_or_create_vk(
            vk_user_id=vk_user_id,
            username=username,
            full_name=full_name,
        )

    async def ensure_from_whatsapp(
        self,
        *,
        whatsapp_phone: str,
        username: str | None,
        full_name: str | None,
    ):
        return await self._users_repo.get_or_create_whatsapp(
            whatsapp_phone=whatsapp_phone,
            username=username,
            full_name=full_name,
        )

    @staticmethod
    def is_admin(user) -> bool:
        return user.role in {UserRole.SUPERADMIN.value, UserRole.ADMIN.value, UserRole.SUPPORT.value}

    @staticmethod
    def is_operator(user) -> bool:
        return user.role in {UserRole.SUPERADMIN.value, UserRole.ADMIN.value}

    @classmethod
    def require_admin(cls, user) -> None:
        if not cls.is_admin(user):
            raise AccessDeniedError("Кажется, вы еще не админ 🙂")

    @classmethod
    def require_operator(cls, user) -> None:
        if not cls.is_operator(user):
            raise AccessDeniedError("Для этого нужны права оператора 🙂")
