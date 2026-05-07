from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.containers import get_container

router = APIRouter()


@router.get("/whatsapp/webhook", response_class=PlainTextResponse)
async def whatsapp_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    container = get_container()
    settings = container.settings
    if not settings.whatsapp_enabled:
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Unexpected webhook mode")
    if settings.whatsapp_verify_enabled and hub_verify_token != settings.whatsapp_verify_token:
        raise HTTPException(status_code=403, detail="Invalid verify token")
    return PlainTextResponse(hub_challenge or "")


@router.post("/whatsapp/webhook", response_class=PlainTextResponse)
async def whatsapp_webhook(request: Request) -> PlainTextResponse:
    container = get_container()
    settings = container.settings
    if not settings.whatsapp_enabled:
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not container.whatsapp_client.verify_signature(body, signature):
        raise HTTPException(status_code=403, detail="Invalid WhatsApp signature")

    payload = await request.json()
    async with container.session_factory() as session:
        services = container.build_services(session)
        await container.whatsapp_bot.handle_webhook(payload, services)
    return PlainTextResponse("ok")
