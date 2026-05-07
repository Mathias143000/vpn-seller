from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.containers import get_container

router = APIRouter()


@router.post("/vk/callback", response_class=PlainTextResponse)
async def vk_callback(request: Request) -> PlainTextResponse:
    container = get_container()
    settings = container.settings
    if not settings.vk_enabled:
        raise HTTPException(status_code=503, detail="VK integration is not configured")

    payload = await request.json()
    event_type = payload.get("type")
    group_id = payload.get("group_id")
    if settings.vk_group_id and group_id not in {None, settings.vk_group_id}:
        raise HTTPException(status_code=403, detail="Unexpected VK group")

    if event_type != "confirmation" and settings.vk_callback_secret:
        if payload.get("secret") != settings.vk_callback_secret:
            raise HTTPException(status_code=403, detail="Invalid VK secret")

    async with container.session_factory() as session:
        services = container.build_services(session)
        response_text = await container.vk_bot.handle_event(payload, services)
    return PlainTextResponse(response_text)
