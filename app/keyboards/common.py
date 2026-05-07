from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_main_menu(*, is_admin: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🟣 Тарифы", callback_data="menu:catalog", style="primary")
    builder.button(text="🟢 Мои покупки", callback_data="menu:orders", style="success")
    builder.button(text="🟠 Поддержка", callback_data="menu:support", style="danger")
    if is_admin:
        builder.button(text="🔵 Админка", callback_data="menu:admin", style="primary")
        builder.adjust(2, 2)
    else:
        builder.adjust(2, 1)
    return builder.as_markup()


def build_back_to_menu(*, text: str = "🏠 Вернуться в меню") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data="menu:root", style="primary")
    builder.adjust(1)
    return builder.as_markup()


def build_back_navigation(
    *,
    back_callback: str,
    back_text: str = "⬅️ Назад",
    include_home: bool = True,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=back_text, callback_data=back_callback, style="danger")
    if include_home and back_callback != "menu:root":
        builder.button(text="🏠 В меню", callback_data="menu:root", style="primary")
        builder.adjust(2)
    else:
        builder.adjust(1)
    return builder.as_markup()


def build_payment_actions(*, order_id: int, payment_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Открыть оплату", url=payment_url, style="success")
    builder.button(
        text="📋 Скопировать номер заказа",
        copy_text={"text": str(order_id)},
        style="primary",
    )
    builder.button(
        text="📝 Скопировать текст для доната",
        copy_text={"text": f"Оплата заказа #{order_id}"},
        style="primary",
    )
    builder.button(text="⬅️ К тарифам", callback_data="menu:catalog", style="danger")
    builder.button(text="🏠 В меню", callback_data="menu:root", style="primary")
    builder.adjust(1, 1, 1, 2)
    return builder.as_markup()
