from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _format_price(value) -> str:
    text = str(value)
    if text.endswith(".00"):
        return text[:-3]
    return text


def _minimum_available_price(item: dict):
    prices = []
    if item.get("inventory_available"):
        prices.append(item.get("inventory_price_value") or item["price_value"])
    if item.get("mtproxy_available"):
        prices.append(item.get("mtproxy_price_value") or item["price_value"])
    if item.get("hiddify_server_options"):
        prices.append(item.get("server_price_value") or item["price_value"])
    if item.get("superkey_available"):
        prices.append(item.get("superkey_price_value") or item["price_value"])
    return min(prices) if prices else item["price_value"]


def build_catalog_keyboard(catalog: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in catalog:
        status_emoji = "🟢" if item["is_available"] else "🔴"
        suffix = f" • от {_format_price(_minimum_available_price(item))} {item['price_currency']}"
        if not item["is_available"]:
            suffix = " • временно нет"
        builder.button(
            text=f"{status_emoji} {item['name']}{suffix}",
            callback_data=f"buy:{item['id']}",
            style="primary" if item["is_available"] else "danger",
        )
    builder.button(text="⬅️ Назад", callback_data="menu:root", style="danger")
    builder.button(text="🧡 Юлия не справилась? 💔", callback_data="menu:support", style="primary")
    builder.adjust(*([1] * len(catalog)), 2)
    return builder.as_markup()


def build_fulfillment_selection(plan_id: int, plan: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if plan.get("inventory_available"):
        builder.button(text="🔑 Готовый ключ", callback_data=f"buy_mode:{plan_id}:inventory", style="success")
    if plan.get("mtproxy_available"):
        builder.button(text="⚡ MTProxy", callback_data=f"buy_mode:{plan_id}:mtproxy", style="success")
    if plan.get("hiddify_server_options"):
        builder.button(text="🖥 Выбрать сервер", callback_data=f"buy_mode:{plan_id}:server", style="primary")
    if plan.get("superkey_available"):
        builder.button(text="🌐 Суперключ", callback_data=f"buy_mode:{plan_id}:superkey", style="primary")
    builder.button(text="⬅️ К тарифам", callback_data="menu:catalog", style="danger")
    builder.button(text="🏠 В меню", callback_data="menu:root", style="primary")
    dynamic_count = (
        int(bool(plan.get("inventory_available")))
        + int(bool(plan.get("mtproxy_available")))
        + int(bool(plan.get("hiddify_server_options")))
        + int(bool(plan.get("superkey_available")))
    )
    builder.adjust(*([1] * dynamic_count), 2)
    return builder.as_markup()


def build_server_selection(plan_id: int, servers: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for server in servers:
        builder.button(
            text=f"🖥 {server['country_name']} • {server['server_name']}",
            callback_data=f"buy_server:{plan_id}:{server['server_id']}",
            style="primary",
        )
    builder.button(text="⬅️ Назад", callback_data=f"buy:{plan_id}", style="danger")
    builder.button(text="🏠 В меню", callback_data="menu:root", style="primary")
    builder.adjust(*([1] * len(servers)), 2)
    return builder.as_markup()


def build_purchase_confirmation(
    plan_id: int,
    *,
    back_callback: str = "menu:catalog",
    back_text: str = "⬅️ Назад",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✨ Оформить заказ", callback_data=f"confirm_buy:{plan_id}", style="success")
    builder.button(text="🎟 Промокод", callback_data=f"promo:ask:{plan_id}", style="primary")
    builder.button(text=back_text, callback_data=back_callback, style="danger")
    builder.button(text="🏠 В меню", callback_data="menu:root", style="primary")
    builder.adjust(1, 1, 2)
    return builder.as_markup()
