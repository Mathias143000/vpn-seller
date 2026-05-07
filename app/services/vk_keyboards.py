from __future__ import annotations

import json
from typing import Any


def build_main_menu_keyboard(*, include_admin: bool = False) -> dict[str, Any]:
    rows = [
        [
            _text_button("🟣 Тарифы", {"command": "menu_catalog"}, color="primary"),
            _text_button("🟢 Мои покупки", {"command": "menu_orders"}, color="positive"),
        ],
        [
            _text_button("🧡 Юлия не справилась? 💔", {"command": "menu_support"}, color="negative"),
        ],
    ]
    if include_admin:
        rows.append([_text_button("🔵 Админка в Telegram", {"command": "menu_admin"}, color="secondary")])
    return _keyboard(rows)


def build_catalog_keyboard(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    for item in catalog:
        emoji = "🟢" if item["is_available"] else "🔴"
        rows.append(
            [
                _text_button(
                    f"{emoji} {item['name']}",
                    {"command": "buy_plan", "plan_id": item["id"]},
                    color="primary" if item["is_available"] else "secondary",
                )
            ]
        )
    rows.append([_text_button("🏠 В меню", {"command": "menu_root"}, color="secondary")])
    return _keyboard(rows)


def build_fulfillment_keyboard(plan: dict[str, Any]) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    plan_id = int(plan["id"])
    if plan.get("inventory_available"):
        rows.append(
            [_text_button("🔑 Готовый ключ", {"command": "buy_mode", "plan_id": plan_id, "mode": "inventory"}, color="positive")]
        )
    if plan.get("mtproxy_available"):
        rows.append([_text_button("⚡ MTProxy", {"command": "buy_mode", "plan_id": plan_id, "mode": "mtproxy"}, color="positive")])
    if plan.get("hiddify_server_options"):
        rows.append(
            [_text_button("🖥 Выбрать сервер", {"command": "buy_mode", "plan_id": plan_id, "mode": "server"}, color="primary")]
        )
    if plan.get("superkey_available"):
        rows.append(
            [_text_button("🌐 Суперключ", {"command": "buy_mode", "plan_id": plan_id, "mode": "superkey"}, color="primary")]
        )
    rows.append([_text_button("⬅️ К тарифам", {"command": "menu_catalog"}, color="secondary")])
    return _keyboard(rows)


def build_server_keyboard(plan_id: int, server_options: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    for server in server_options:
        rows.append(
            [
                _text_button(
                    f"{server['country_name']} • {server['server_name']}",
                    {"command": "buy_server", "plan_id": plan_id, "server_id": server["server_id"]},
                    color="primary",
                )
            ]
        )
    rows.append([_text_button("⬅️ Назад", {"command": "buy_plan", "plan_id": plan_id}, color="secondary")])
    return _keyboard(rows)


def build_confirmation_keyboard(
    *,
    plan_id: int,
    fulfillment_mode: str,
    server_id: int | None = None,
) -> dict[str, Any]:
    rows = [
        [
            _text_button(
                "✨ Оформить заказ",
                {
                    "command": "confirm_buy",
                    "plan_id": plan_id,
                    "mode": fulfillment_mode,
                    "server_id": server_id,
                },
                color="positive",
            )
        ],
        [_text_button("⬅️ Назад", {"command": "buy_plan", "plan_id": plan_id}, color="secondary")],
    ]
    return _keyboard(rows)


def build_payment_keyboard(*, payment_url: str) -> dict[str, Any]:
    rows = [
        [_open_link_button("💳 Открыть оплату", payment_url)],
        [
            _text_button("🟢 Мои покупки", {"command": "menu_orders"}, color="positive"),
            _text_button("🏠 В меню", {"command": "menu_root"}, color="secondary"),
        ],
    ]
    return _keyboard(rows)


def _keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"one_time": False, "inline": False, "buttons": rows}


def _text_button(label: str, payload: dict[str, Any], *, color: str) -> dict[str, Any]:
    return {
        "action": {
            "type": "text",
            "label": label,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
        "color": color,
    }


def _open_link_button(label: str, link: str) -> dict[str, Any]:
    return {
        "action": {
            "type": "open_link",
            "label": label,
            "link": link,
        }
    }
