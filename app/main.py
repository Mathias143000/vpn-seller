from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.containers import get_container
from app.runtime import configure_runtime
from app.web.files import router as files_router
from app.web.health import router as health_router
from app.web.subscriptions import router as subscriptions_router
from app.web.vk_webhook import router as vk_router
from app.web.whatsapp_webhook import router as whatsapp_router
from app.web.webhook import router as telegram_router

configure_runtime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    container = get_container()
    await container.startup()
    try:
        yield
    finally:
        await container.shutdown()


def create_app() -> FastAPI:
    application = FastAPI(title="vpn-seller", lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(files_router)
    application.include_router(subscriptions_router)
    application.include_router(telegram_router)
    application.include_router(vk_router)
    application.include_router(whatsapp_router)
    return application


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--polling", action="store_true", help="Run Telegram bot via long polling")
    args = parser.parse_args()
    container = get_container()
    if args.polling:
        asyncio.run(container.run_polling())
    else:
        uvicorn.run("app.main:app", host=container.settings.app_host, port=container.settings.app_port, reload=False)


if __name__ == "__main__":
    main()
