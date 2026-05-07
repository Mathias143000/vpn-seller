from __future__ import annotations

import logging

from aiogram import BaseMiddleware

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        logger.info("Incoming update: %s", type(event).__name__)
        return await handler(event, data)

