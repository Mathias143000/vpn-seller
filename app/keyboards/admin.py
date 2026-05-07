from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Импорт XLSX", callback_data="admin:import", style="primary")
    builder.button(text="📤 Экспорт: всё", callback_data="admin:export:inventory:all", style="success")
    builder.button(text="🟢 Экспорт: свободные", callback_data="admin:export:inventory:available", style="success")
    builder.button(text="🔐 Экспорт: выданные", callback_data="admin:export:inventory:issued", style="success")
    builder.button(text="🧾 Экспорт заказов", callback_data="admin:export:orders:all", style="success")
    builder.button(text="📦 Остатки", callback_data="admin:stock", style="primary")
    builder.button(text="🖥 Hiddify", callback_data="admin:hiddify", style="primary")
    builder.button(text="🎟 Промокоды", callback_data="admin:promos", style="success")
    builder.button(text="📣 Оповещения", callback_data="admin:notifications", style="primary")
    builder.button(text="🏠 В меню", callback_data="menu:root", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_import_waiting_actions() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Вернуться в админку", callback_data="admin:import:back", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_import_confirmation() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить импорт", callback_data="admin:import:confirm", style="success")
    builder.button(text="⬅️ Вернуться", callback_data="admin:import:cancel", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_order_actions(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить оплату", callback_data=f"admin:order:confirm:{order_id}", style="success")
    builder.button(text="❌ Отменить заказ", callback_data=f"admin:order:cancel:{order_id}", style="danger")
    builder.button(text="🔁 Повторно отправить ключ", callback_data=f"admin:order:resend:{order_id}", style="primary")
    builder.button(text="💸 Пометить refund", callback_data=f"admin:order:refund:{order_id}", style="primary")
    builder.button(text="🛠 Заменить ключ", callback_data=f"admin:order:replace:{order_id}", style="primary")
    builder.button(text="⬅️ В админку", callback_data="menu:admin", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_hiddify_menu(servers) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Подключить сервер", callback_data="admin:hiddify:add", style="success")
    builder.button(text="📊 Нагрузка серверов", callback_data="admin:hiddify:load", style="primary")
    builder.button(text="📸 Собрать snapshot сейчас", callback_data="admin:hiddify:snapshots:collect", style="primary")
    for server in servers:
        status_emoji = "🟢" if server.is_active else "⚪"
        health_emoji = {
            "healthy": "✅",
            "unhealthy": "⚠️",
            "unknown": "❔",
        }.get(server.last_health_status, "❔")
        builder.button(
            text=f"{status_emoji} {health_emoji} {server.name} • {server.country_name}",
            callback_data=f"admin:hiddify:server:{server.id}",
            style="primary",
        )
    builder.button(text="⬅️ В админку", callback_data="menu:admin", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_hiddify_add_options() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✍️ Ручной ввод", callback_data="admin:hiddify:add:manual", style="primary")
    builder.button(text="📥 Импорт XLSX", callback_data="admin:hiddify:add:xlsx", style="success")
    builder.button(text="⬅️ К серверам", callback_data="admin:hiddify:cancel", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_hiddify_import_waiting_actions() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📎 Отправить шаблон ещё раз", callback_data="admin:hiddify:add:xlsx", style="primary")
    builder.button(text="⬅️ К серверам", callback_data="admin:hiddify:cancel", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_hiddify_import_confirmation() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить импорт", callback_data="admin:hiddify:import:confirm", style="success")
    builder.button(text="⬅️ Отменить", callback_data="admin:hiddify:import:cancel", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_hiddify_server_actions(*, server_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Проверить", callback_data=f"admin:hiddify:check:{server_id}", style="primary")
    builder.button(text="📊 Нагрузка серверов", callback_data="admin:hiddify:load", style="primary")
    builder.button(text="📸 Собрать snapshot сейчас", callback_data="admin:hiddify:snapshots:collect", style="primary")
    builder.button(
        text="⏸ Отключить" if is_active else "▶️ Включить",
        callback_data=f"admin:hiddify:toggle:{server_id}",
        style="danger" if is_active else "success",
    )
    builder.button(text="⬅️ К серверам", callback_data="admin:hiddify", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_hiddify_load_actions() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📸 Собрать snapshot сейчас", callback_data="admin:hiddify:snapshots:collect", style="primary")
    builder.button(text="⬅️ К серверам", callback_data="admin:hiddify:cancel", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def build_hiddify_add_cancel() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К серверам", callback_data="admin:hiddify:cancel", style="danger")
    builder.adjust(1)
    return builder.as_markup()
