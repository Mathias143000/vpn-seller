from __future__ import annotations

from aiogram import BaseMiddleware


class AuthMiddleware(BaseMiddleware):
    def __init__(self, container) -> None:
        self._container = container

    async def __call__(self, handler, event, data):
        telegram_user = data.get("event_from_user")
        if telegram_user is None:
            return await handler(event, data)

        async with self._container.session_factory() as session:
            services = self._container.build_services(session)
            async with session.begin():
                app_user = await services.users.ensure_from_telegram(telegram_user)
            data["session"] = session
            data["services"] = services
            data["app_user"] = app_user
            data["container"] = self._container
            return await handler(event, data)

