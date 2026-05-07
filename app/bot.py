from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.handlers.admin_export import router as admin_export_router
from app.handlers.admin_hiddify import router as admin_hiddify_router
from app.handlers.admin_import import router as admin_import_router
from app.handlers.admin_notifications import router as admin_notifications_router
from app.handlers.admin_orders import router as admin_orders_router
from app.handlers.admin_promos import router as admin_promos_router
from app.handlers.admin_settings import router as admin_settings_router
from app.handlers.admin_stock import router as admin_stock_router
from app.handlers.catalog import router as catalog_router
from app.handlers.my_orders import router as my_orders_router
from app.handlers.purchase import router as purchase_router
from app.handlers.start import router as start_router
from app.handlers.support import router as support_router
from app.middlewares.admin import AdminMiddleware
from app.middlewares.auth import AuthMiddleware
from app.middlewares.logging import LoggingMiddleware


def create_dispatcher(container) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(LoggingMiddleware())
    dispatcher.update.outer_middleware(AuthMiddleware(container))
    dispatcher.update.outer_middleware(AdminMiddleware())

    dispatcher.include_router(start_router)
    dispatcher.include_router(catalog_router)
    dispatcher.include_router(purchase_router)
    dispatcher.include_router(my_orders_router)
    dispatcher.include_router(support_router)
    dispatcher.include_router(admin_import_router)
    dispatcher.include_router(admin_export_router)
    dispatcher.include_router(admin_hiddify_router)
    dispatcher.include_router(admin_notifications_router)
    dispatcher.include_router(admin_orders_router)
    dispatcher.include_router(admin_promos_router)
    dispatcher.include_router(admin_settings_router)
    dispatcher.include_router(admin_stock_router)
    return dispatcher
