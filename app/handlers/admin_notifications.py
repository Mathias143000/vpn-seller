from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.keyboards.common import build_back_navigation
from app.services.exceptions import AccessDeniedError, DomainError
from app.services.users import UsersService

router = Router()


def _notifications_help() -> str:
    return (
        "<b>Оповещения 📣</b>\n\n"
        "Если нужно быстро предупредить клиентов о форс-мажоре, у тебя есть два инструмента:\n\n"
        "<b>1. Личное сообщение клиенту</b>\n"
        "<code>/admin_notify @username Текст сообщения</code>\n"
        "<code>/admin_notify 1972261208 Текст сообщения</code>\n"
        "<code>/admin_notify 123456789 Текст сообщения</code> — также сработает для VK-пользователя, если это его VK ID.\n\n"
        "<b>2. Массовая рассылка всем клиентам</b>\n"
        "<code>/admin_broadcast Важный текст для всех клиентов</code>\n\n"
        "В рассылку попадают пользователи, у которых уже есть заказы. Все такие действия пишутся в audit log."
    )


@router.callback_query(F.data == "admin:notifications")
async def admin_notifications_callback(callback: CallbackQuery, app_user, **_: dict) -> None:
    try:
        UsersService.require_admin(app_user)
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
        return
    await callback.message.edit_text(
        _notifications_help(),
        reply_markup=build_back_navigation(back_callback="menu:admin", back_text="⬅️ В админку"),
    )
    await callback.answer()


@router.message(Command("admin_notify"))
async def admin_notify_command(message: Message, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_operator(app_user)
    except AccessDeniedError:
        await message.answer("Для этого нужны права оператора 🙂")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Использование:\n"
            "<code>/admin_notify @username Текст сообщения</code>\n"
            "<code>/admin_notify 1972261208 Текст сообщения</code>"
        )
        return

    target = parts[1].strip()
    text = parts[2].strip()
    try:
        user_label = await services.communications.send_direct_message(
            target=target,
            text=text,
            actor_user_id=app_user.id,
        )
    except DomainError as exc:
        await message.answer(str(exc))
        return

    await message.answer(f"Сообщение отправлено пользователю <code>{user_label}</code> ✨")


@router.message(Command("admin_broadcast"))
async def admin_broadcast_command(message: Message, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_operator(app_user)
    except AccessDeniedError:
        await message.answer("Для этого нужны права оператора 🙂")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование:\n<code>/admin_broadcast Важный текст для всех клиентов</code>")
        return

    result = await services.communications.broadcast_to_customers(
        text=parts[1],
        actor_user_id=app_user.id,
    )
    failed_count = len(result["failed"])
    await message.answer(
        (
            "<b>Рассылка завершена</b>\n\n"
            f"Всего клиентов: <b>{result['total']}</b>\n"
            f"Успешно отправлено: <b>{result['sent']}</b>\n"
            f"Не доставлено: <b>{failed_count}</b>"
        )
    )
