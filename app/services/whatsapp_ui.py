from __future__ import annotations

from typing import Any


def build_main_menu_buttons() -> list[dict[str, str]]:
    return [
        {"id": "menu_catalog", "title": "Тарифы"},
        {"id": "menu_orders", "title": "Покупки"},
        {"id": "menu_support", "title": "Поддержка"},
    ]


def build_catalog_sections(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, str]] = []
    for item in catalog[:10]:
        availability = "доступно" if item["is_available"] else "временно нет"
        prices = []
        if item.get("inventory_available"):
            prices.append(item.get("inventory_price_value") or item["price_value"])
        if item.get("mtproxy_available"):
            prices.append(item.get("mtproxy_price_value") or item["price_value"])
        if item.get("hiddify_server_options"):
            prices.append(item.get("server_price_value") or item["price_value"])
        if item.get("superkey_available"):
            prices.append(item.get("superkey_price_value") or item["price_value"])
        price = min(prices) if prices else item["price_value"]
        rows.append(
            {
                "id": f"buy_plan:{item['id']}",
                "title": item["name"][:24],
                "description": f"от {price} {item['price_currency']} • {availability}"[:72],
            }
        )
    return [{"title": "Тарифы", "rows": rows}]


def build_fulfillment_buttons(plan: dict[str, Any]) -> list[dict[str, str]]:
    buttons: list[dict[str, str]] = []
    plan_id = int(plan["id"])
    if plan.get("inventory_available"):
        buttons.append({"id": f"buy_mode:{plan_id}:inventory", "title": "Готовый ключ"})
    if plan.get("mtproxy_available"):
        buttons.append({"id": f"buy_mode:{plan_id}:mtproxy", "title": "MTProxy"})
    if plan.get("hiddify_server_options"):
        buttons.append({"id": f"buy_mode:{plan_id}:server", "title": "Выбрать сервер"})
    if plan.get("superkey_available"):
        buttons.append({"id": f"buy_mode:{plan_id}:superkey", "title": "Суперключ"})
    return buttons[:3]


def build_server_sections(plan_id: int, server_options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, str]] = []
    for server in server_options[:10]:
        rows.append(
            {
                "id": f"buy_server:{plan_id}:{server['server_id']}",
                "title": server["server_name"][:24],
                "description": server["country_name"][:72],
            }
        )
    return [{"title": "Серверы", "rows": rows}]


def build_confirmation_buttons(plan_id: int, fulfillment_mode: str, server_id: int | None = None) -> list[dict[str, str]]:
    command = f"confirm_buy:{plan_id}:{fulfillment_mode}"
    if server_id is not None:
        command = f"{command}:{server_id}"
    return [
        {"id": command, "title": "Оформить"},
        {"id": "menu_catalog", "title": "Назад"},
    ]
