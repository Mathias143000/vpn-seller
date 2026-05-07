from __future__ import annotations

from aiogram import BaseMiddleware


class AdminMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        return await handler(event, data)

