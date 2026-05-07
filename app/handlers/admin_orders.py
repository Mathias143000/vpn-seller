from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.keyboards.admin import build_order_actions
from app.services.exceptions import AccessDeniedError, DomainError
from app.services.users import UsersService

router = Router()


def _format_order(order) -> str:
    return (
        f"<b>Заказ #{order.id}</b>\n"
        f"Статус: <b>{order.status}</b>\n"
        f"Сумма: <b>{order.amount_value} {order.amount_currency}</b>\n"
        f"payment_id: <code>{order.provider_payment_id}</code>\n"
        f"reserved_key_id: {order.reserved_key_id}\n"
        f"issued_key_id: {order.issued_key_id}\n"
        f"delivery_status: {order.delivery_status}"
    )


@router.message(Command("admin_order"))
async def admin_order_command(message: Message, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, у вас пока нет доступа к админке 🙂")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/admin_order order_id|payment_id|telegram_user_id|vk_user_id|whatsapp_phone|username</code>"
        )
        return
    orders = await services.orders.search_orders(parts[1].strip())
    if not orders:
        await message.answer("Ничего не нашлось 👀")
        return
    for order in orders[:5]:
        await message.answer(_format_order(order), reply_markup=build_order_actions(order.id))


@router.callback_query(F.data.startswith("admin:order:"))
async def admin_order_action_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    _, _, action, order_id_raw = callback.data.split(":", maxsplit=3)
    order_id = int(order_id_raw)
    try:
        if action in {"confirm", "cancel", "refund", "replace"}:
            UsersService.require_operator(app_user)
        else:
            UsersService.require_admin(app_user)

        if action == "confirm":
            order_status, issued_key_id = await services.payments.confirm_manual_payment(
                order_id=order_id,
                actor_user_id=app_user.id,
            )
            await services.delivery.process_pending_jobs()
            if issued_key_id is not None:
                text = f"Оплата по заказу #{order_id} подтверждена, ключ #{issued_key_id} уже отправлен клиенту ✅"
            else:
                text = (
                    f"Оплата по заказу #{order_id} подтверждена, но ключ пока не выдан.\n"
                    f"Текущий статус: <b>{order_status}</b>"
                )
        elif action == "cancel":
            await services.orders.cancel_unpaid_order(order_id=order_id, actor_user_id=app_user.id)
            text = f"Заказ #{order_id} отменен."
        elif action == "refund":
            await services.orders.mark_refunded(order_id=order_id, actor_user_id=app_user.id)
            text = f"Заказ #{order_id} помечен как refunded."
        elif action == "replace":
            new_key_id = await services.issuing.replace_access_for_issued_order(order_id=order_id, actor_user_id=app_user.id)
            await services.delivery.process_pending_jobs()
            text = f"Для заказа #{order_id} выдан replacement key #{new_key_id}."
        elif action == "resend":
            await services.orders.enqueue_resend(order_id=order_id, actor_user_id=app_user.id)
            text = f"Для заказа #{order_id} поставлена повторная отправка ключа."
        else:
            text = "Неизвестное действие."
        await callback.message.answer(text)
        await callback.answer("Готово ✨")
    except (AccessDeniedError, DomainError) as exc:
        await callback.answer(str(exc), show_alert=True)
