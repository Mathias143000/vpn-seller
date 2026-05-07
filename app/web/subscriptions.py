from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.containers import get_container
from app.services.exceptions import NotFoundError, ProvisioningError

router = APIRouter()


@router.get("/subscriptions/{token}")
async def aggregate_subscription(token: str) -> Response:
    container = get_container()
    try:
        async with container.session_factory() as session:
            services = container.build_services(session)
            payload = await services.subscriptions.build_subscription_payload(token)
        return Response(
            content=payload,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
