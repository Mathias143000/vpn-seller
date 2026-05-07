from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.db.models import PlanProvisioningMode
from app.keyboards.catalog import build_catalog_keyboard
from app.handlers.purchase import _fmt_money

router = Router()


def _content_text(content, key: str, default: str) -> str:
    if content is None:
        return default
    return content.get(key, default)


def _format_catalog(catalog: list[dict], content=None) -> str:
    lines = [
        _content_text(
            content,
            "catalog.header",
            "<b>Тарифы Юлии ✨</b>\nВыбирай вариант, а я после подтверждения оплаты отправлю доступ прямо в этот чат.",
        ),
        "",
    ]
    for item in catalog:
        status_emoji = "🟢" if item["is_available"] else "🔴"
        available_prices = [
            item.get("inventory_price_value") if item.get("inventory_available") else None,
            item.get("mtproxy_price_value") if item.get("mtproxy_available") else None,
            item.get("server_price_value") if item.get("hiddify_server_options") else None,
            item.get("superkey_price_value") if item.get("superkey_available") else None,
        ]
        prices = [price for price in available_prices if price is not None]
        price_label = f"от {_fmt_money(min(prices))}" if prices else _fmt_money(item["price_value"])
        lines.append(
            f"{status_emoji} <b>{item['name']}</b> • {item['duration_days']} дн. • "
            f"{price_label} {item['price_currency']}"
        )
        lines.append(_format_availability(item))
        if item.get("hiddify_server_options"):
            server_lines = ", ".join(
                f"{server['country_name']} — {server['server_name']}" for server in item["hiddify_server_options"]
            )
            lines.append(f"Серверы: <b>{server_lines}</b>")
        if item.get("superkey_available"):
            countries = sorted({server["country_name"] for server in item["hiddify_server_options"]})
            lines.append(f"Суперключ: <b>{', '.join(countries)}</b>")
        if item["description"]:
            lines.append(item["description"])
        lines.append("")
    value_note = _content_text(content, "catalog.value_note", "")
    if value_note:
        lines.append(value_note)
    return "\n".join(lines).strip()


def _format_availability(item: dict) -> str:
    if not item["is_available"]:
        return _format_unavailable_reason(item)

    inventory_available = item.get("inventory_available")
    mtproxy_available = item.get("mtproxy_available")
    server_options = item.get("hiddify_server_options", [])
    if mtproxy_available:
        return f"MTProxy можно выдать на активных серверах: <b>{item.get('mtproxy_server_count', 0)}</b>"
    if inventory_available and server_options:
        return (
            f"Готовых ключей: <b>{item['available_count']}</b>. "
            "Можно взять ключ со склада или выбрать конкретный сервер."
        )
    if inventory_available:
        return f"Готовых ключей в наличии: <b>{item['available_count']}</b>"
    if server_options:
        if item.get("superkey_available"):
            return "Доступ собираю через серверы Hiddify. Можно выбрать сервер или оформить суперключ."
        return "Доступ собираю через серверы Hiddify. Можно выбрать конкретный сервер."
    return "Сейчас этот тариф временно недоступен."


def _format_unavailable_reason(item: dict) -> str:
    provisioning_mode = item["provisioning_mode"]
    has_hiddify = bool(item.get("hiddify_server_options"))
    if provisioning_mode == PlanProvisioningMode.MTPROXY.value:
        return "Сейчас нет активных серверов, на которых можно выдать MTProxy."
    if provisioning_mode == PlanProvisioningMode.INVENTORY.value:
        return "Сейчас закончились подготовленные ключи для этого тарифа."
    if provisioning_mode == PlanProvisioningMode.HIDDIFY.value:
        return "Для этого тарифа пока не подключён ни один активный Hiddify-сервер."
    if item["available_count"] <= 0 and not has_hiddify:
        return "Сейчас нет ни подготовленных ключей, ни подключённых Hiddify-серверов."
    return "Сейчас этот тариф временно недоступен."


@router.message(Command("catalog"))
async def catalog_command(message: Message, services, **_: dict) -> None:
    catalog = await services.plans.get_catalog()
    await message.answer(_format_catalog(catalog, services.content), reply_markup=build_catalog_keyboard(catalog))


@router.callback_query(F.data == "menu:catalog")
async def catalog_callback(callback: CallbackQuery, services, **_: dict) -> None:
    catalog = await services.plans.get_catalog()
    await callback.message.edit_text(_format_catalog(catalog, services.content), reply_markup=build_catalog_keyboard(catalog))
    await callback.answer()
