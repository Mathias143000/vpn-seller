from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.keyboards.common import build_back_to_menu

router = Router()


def _status_label(status: str) -> str:
    mapping = {
        "created": "🆕 Создан",
        "pending_payment": "🟡 Ждёт оплаты",
        "paid": "🟠 Оплата подтверждена",
        "issued": "🟢 Ключ отправлен",
        "paid_but_not_issued": "🚨 Оплата есть, выдача задержалась",
        "canceled": "⚪ Отменён",
        "refunded": "🔁 Возврат",
        "payment_failed": "🔴 Ошибка оплаты",
        "expired_reservation": "⌛ Резерв истёк",
    }
    return mapping.get(status, status)


def _format_orders(orders) -> str:
    if not orders:
        return (
            "<b>Твои покупки 🧾</b>\n\n"
            "Пока здесь пусто 🙂\n"
            "Как только оформишь первый заказ, я покажу его тут."
        )

    lines = ["<b>Твои покупки 🧾</b>", ""]
    for order in orders:
        lines.append(
            f"Заказ <b>#{order.id}</b>\n"
            f"Статус: {_status_label(order.status)}\n"
            f"Сумма: <b>{order.amount_value} {order.amount_currency}</b>\n"
            f"Создан: {order.created_at:%Y-%m-%d %H:%M}"
        )
        lines.append("")
    return "\n".join(lines).strip()


@router.message(Command("my_orders"))
async def my_orders_command(message: Message, app_user, services, **_: dict) -> None:
    orders = await services.orders.list_user_orders(app_user.id)
    await message.answer(_format_orders(orders), reply_markup=build_back_to_menu())


@router.callback_query(F.data == "menu:orders")
async def my_orders_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    orders = await services.orders.list_user_orders(app_user.id)
    await callback.message.edit_text(_format_orders(orders), reply_markup=build_back_to_menu())
    await callback.answer()
