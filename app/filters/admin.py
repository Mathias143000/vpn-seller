from __future__ import annotations

from aiogram.filters import BaseFilter

from app.services.users import UsersService


class AdminFilter(BaseFilter):
    async def __call__(self, *args, app_user=None, **kwargs) -> bool:
        return bool(app_user and UsersService.is_admin(app_user))

