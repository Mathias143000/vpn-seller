from __future__ import annotations

from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request

from app.containers import get_container

router = APIRouter()


@router.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict:
    container = get_container()
    if secret != container.settings.bot_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": container.bot})
    await container.dispatcher.feed_update(container.bot, update)
    return {"ok": True}

