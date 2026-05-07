from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.containers import get_container

router = APIRouter()


@router.get("/health")
async def healthcheck() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def readiness() -> dict:
    container = get_container()
    try:
        async with container.engine.begin() as connection:
            await connection.execute(text("SELECT 1"))
        async with container.session_factory() as session:
            services = container.build_services(session)
            catalog = await services.plans.get_catalog()
        return {"status": "ready", "plans_count": len(catalog)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Not ready: {exc}") from exc

