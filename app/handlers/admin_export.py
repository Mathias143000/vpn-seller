from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.services.exceptions import AccessDeniedError
from app.services.users import UsersService

router = Router()


async def _send_inventory_export(target: Message, *, export_status: str | None, app_user, services) -> None:
    UsersService.require_admin(app_user)
    content = await services.xlsx_export.export_inventory(status=export_status, actor_user_id=app_user.id)
    filename_suffix = export_status or "all"
    await target.answer_document(
        BufferedInputFile(content, filename=f"inventory_{filename_suffix}.xlsx"),
        caption="Экспорт склада готов ✨",
    )


async def _send_orders_export(target: Message, *, order_status: str | None, app_user, services) -> None:
    UsersService.require_admin(app_user)
    content = await services.xlsx_export.export_orders(status=order_status, actor_user_id=app_user.id)
    filename_suffix = order_status or "all"
    await target.answer_document(
        BufferedInputFile(content, filename=f"orders_{filename_suffix}.xlsx"),
        caption="Экспорт заказов готов ✨",
    )


@router.message(Command("admin_export"))
async def admin_export_command(message: Message, app_user, services, **_: dict) -> None:
    try:
        parts = message.text.split(maxsplit=1)
        if len(parts) == 1:
            await _send_inventory_export(message, export_status=None, app_user=app_user, services=services)
            await _send_orders_export(message, order_status=None, app_user=app_user, services=services)
            return

        export_target = parts[1].strip()
        if export_target == "orders":
            await _send_orders_export(message, order_status=None, app_user=app_user, services=services)
            return

        await _send_inventory_export(message, export_status=export_target, app_user=app_user, services=services)
    except AccessDeniedError:
        await message.answer("Кажется, у тебя пока нет доступа к админке 🙂")


@router.callback_query(F.data.startswith("admin:export:inventory:"))
async def admin_inventory_export_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    try:
        export_status = callback.data.split(":")[-1]
        if export_status == "all":
            export_status = None
        await _send_inventory_export(callback.message, export_status=export_status, app_user=app_user, services=services)
        await callback.answer("Экспорт склада подготовлен ✨")
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)


@router.callback_query(F.data.startswith("admin:export:orders:"))
async def admin_orders_export_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    try:
        order_status = callback.data.split(":")[-1]
        if order_status == "all":
            order_status = None
        await _send_orders_export(callback.message, order_status=order_status, app_user=app_user, services=services)
        await callback.answer("Экспорт заказов подготовлен ✨")
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
