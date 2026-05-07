from __future__ import annotations

import json
from typing import Any
from types import SimpleNamespace

from app.config import Settings
from app.db.models import OrderFulfillmentMode
from app.handlers.catalog import _format_catalog
from app.handlers.my_orders import _format_orders
from app.handlers.purchase import (
    _build_fulfillment_prompt,
    _build_plan_confirmation_text,
    _find_plan,
    _fulfillment_label,
    _plan_unavailable_message,
)
from app.handlers.start import _welcome_text
from app.handlers.support import _support_text
from app.services.exceptions import DomainError
from app.services.notifications import NotificationService
from app.services.transactions import transactional
from app.services.vk_api import VKApiClient
from app.services.vk_keyboards import (
    build_catalog_keyboard,
    build_confirmation_keyboard,
    build_fulfillment_keyboard,
    build_main_menu_keyboard,
    build_payment_keyboard,
    build_server_keyboard,
)


class VkBotService:
    def __init__(self, settings: Settings, vk_client: VKApiClient, notification_service: NotificationService) -> None:
        self._settings = settings
        self._vk_client = vk_client
        self._notification_service = notification_service

    async def handle_event(self, payload: dict[str, Any], services) -> str:
        event_type = payload.get("type")
        if event_type == "confirmation":
            return self._settings.vk_confirmation_token or "ok"
        if event_type not in {"message_new", "message_event"}:
            return "ok"

        message = self._extract_message(payload)
        if not message:
            return "ok"

        app_user = await self._ensure_vk_user(services, message)
        command = self._parse_command(message)
        await self._dispatch_command(command, app_user, services)
        return "ok"

    @staticmethod
    def _svc(services, name: str):
        if isinstance(services, dict):
            if name in services:
                return services[name]
            if name == "notifications" and "notification" in services:
                return services["notification"]
            raise KeyError(name)
        return getattr(services, name)

    async def _ensure_vk_user(self, services, message: dict[str, Any]):
        vk_user_id = int(message.get("from_id") or message.get("user_id") or 0)
        profile = None
        try:
            profile = await self._vk_client.get_user_profile(vk_user_id)
        except Exception:
            profile = None

        username = profile.get("screen_name") if isinstance(profile, dict) else None
        full_name = None
        if isinstance(profile, dict):
            full_name = " ".join(
                part for part in [profile.get("first_name"), profile.get("last_name")] if isinstance(part, str) and part
            ).strip()
        full_name = full_name or f"VK User {vk_user_id}"

        session = self._svc(services, "session")
        users_service = self._svc(services, "users")
        async with transactional(session):
            return await users_service.ensure_from_vk(
                vk_user_id=vk_user_id,
                username=username,
                full_name=full_name,
            )

    @staticmethod
    def _extract_message(payload: dict[str, Any]) -> dict[str, Any] | None:
        event_type = payload.get("type")
        if event_type == "message_new":
            obj = payload.get("object") or {}
            if isinstance(obj, dict) and isinstance(obj.get("message"), dict):
                return obj["message"]
            if isinstance(obj, dict):
                return obj
        if event_type == "message_event":
            obj = payload.get("object") or {}
            if isinstance(obj, dict):
                return {
                    "from_id": obj.get("user_id"),
                    "peer_id": obj.get("peer_id"),
                    "payload": json.dumps(obj.get("payload") or {}, ensure_ascii=False),
                    "text": "",
                }
        return None

    def _parse_command(self, message: dict[str, Any]) -> dict[str, Any]:
        payload_raw = message.get("payload")
        if isinstance(payload_raw, str) and payload_raw.strip():
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("command"):
                return payload

        text = str(message.get("text") or "").strip().lower()
        if text.startswith("/promo ") or text.startswith("promo "):
            return {"command": "promo_set", "code": text.split(maxsplit=1)[1]}
        mapping = {
            "start": {"command": "menu_root"},
            "начать": {"command": "menu_root"},
            "/start": {"command": "menu_root"},
            "меню": {"command": "menu_root"},
            "тарифы": {"command": "menu_catalog"},
            "каталог": {"command": "menu_catalog"},
            "мои покупки": {"command": "menu_orders"},
            "покупки": {"command": "menu_orders"},
            "поддержка": {"command": "menu_support"},
        }
        return mapping.get(text, {"command": "menu_root"})

    async def _dispatch_command(self, command: dict[str, Any], app_user, services) -> None:
        command_name = command.get("command")
        if command_name == "menu_root":
            await self._show_welcome(app_user)
            return
        if command_name == "menu_catalog":
            await self._show_catalog(app_user, services)
            return
        if command_name == "menu_orders":
            await self._show_orders(app_user, services)
            return
        if command_name == "menu_support":
            await self._show_support(app_user, services)
            return
        if command_name == "promo_set":
            await self._set_promo(app_user, services, str(command.get("code") or ""))
            return
        if command_name == "menu_admin":
            await self._notification_service.send_text(
                "Админка Юлии пока живёт в Telegram. Если нужно, открой Telegram-бота и перейди в раздел администратора.",
                user=app_user,
                keyboard=build_main_menu_keyboard(),
            )
            return
        if command_name == "buy_plan":
            await self._show_plan_entry(app_user, services, int(command["plan_id"]))
            return
        if command_name == "buy_mode":
            await self._show_buy_mode(app_user, services, int(command["plan_id"]), str(command["mode"]))
            return
        if command_name == "buy_server":
            await self._show_server_confirmation(app_user, services, int(command["plan_id"]), int(command["server_id"]))
            return
        if command_name == "confirm_buy":
            await self._confirm_buy(
                app_user,
                services,
                plan_id=int(command["plan_id"]),
                fulfillment_mode=str(command["mode"]),
                server_id=int(command["server_id"]) if command.get("server_id") else None,
            )
            return
        await self._show_welcome(app_user)

    async def _set_promo(self, app_user, services, code: str) -> None:
        try:
            preview = await self._svc(services, "promos").set_active_for_user(user_id=app_user.id, code=code)
        except DomainError as exc:
            await self._notification_service.send_text(
                f"Не смогла применить промокод: {exc}",
                user=app_user,
                keyboard=build_main_menu_keyboard(),
            )
            return
        await self._notification_service.send_text(
            f"Промокод {preview.code} активирован. Я применю его к следующему заказу, если он подходит по условиям.",
            user=app_user,
            keyboard=build_main_menu_keyboard(),
        )

    async def _show_welcome(self, app_user) -> None:
        await self._notification_service.send_text(
            _welcome_text(),
            user=app_user,
            keyboard=build_main_menu_keyboard(),
        )

    async def _show_catalog(self, app_user, services) -> None:
        catalog = await self._svc(services, "plans").get_catalog()
        content = self._svc(services, "content") if isinstance(services, dict) and "content" in services else getattr(services, "content", None)
        await self._notification_service.send_text(
            _format_catalog(catalog, content),
            user=app_user,
            keyboard=build_catalog_keyboard(catalog),
        )

    async def _show_orders(self, app_user, services) -> None:
        orders = await self._svc(services, "orders").list_user_orders(app_user.id)
        await self._notification_service.send_text(
            _format_orders(orders),
            user=app_user,
            keyboard=build_main_menu_keyboard(),
        )

    async def _show_support(self, app_user, services) -> None:
        await self._notification_service.send_text(
            await _support_text(
                services
                if not isinstance(services, dict)
                else SimpleNamespace(
                    notifications=self._notification_service,
                    shop_settings=services.get("shop_settings"),
                    content=services.get("content"),
                )
            ),
            user=app_user,
            keyboard=build_main_menu_keyboard(),
        )

    async def _show_plan_entry(self, app_user, services, plan_id: int) -> None:
        catalog = await self._svc(services, "plans").get_catalog()
        plan = _find_plan(catalog, plan_id)
        if plan is None:
            await self._notification_service.send_text(
                "Не смогла найти этот тариф 😕",
                user=app_user,
                keyboard=build_catalog_keyboard(catalog),
            )
            return
        if not plan["is_available"]:
            await self._notification_service.send_text(
                _plan_unavailable_message(plan),
                user=app_user,
                keyboard=build_catalog_keyboard(catalog),
            )
            return

        inventory_available = bool(plan.get("inventory_available"))
        mtproxy_available = bool(plan.get("mtproxy_available"))
        server_options = plan.get("hiddify_server_options", [])
        superkey_available = bool(plan.get("superkey_available"))

        if (inventory_available or mtproxy_available) and not server_options and not superkey_available:
            await self._show_confirmation(
                app_user,
                plan,
                fulfillment_mode=OrderFulfillmentMode.MTPROXY.value
                if mtproxy_available
                else OrderFulfillmentMode.INVENTORY.value,
            )
            return

        if not inventory_available and not mtproxy_available and len(server_options) == 1 and not superkey_available:
            await self._show_server_confirmation(app_user, services, plan_id, int(server_options[0]["server_id"]))
            return

        await self._notification_service.send_text(
            _build_fulfillment_prompt(plan),
            user=app_user,
            keyboard=build_fulfillment_keyboard(plan),
        )

    async def _show_buy_mode(self, app_user, services, plan_id: int, mode: str) -> None:
        catalog = await self._svc(services, "plans").get_catalog()
        plan = _find_plan(catalog, plan_id)
        if plan is None:
            await self._show_catalog(app_user, services)
            return

        if mode == "inventory":
            await self._show_confirmation(app_user, plan, fulfillment_mode=OrderFulfillmentMode.INVENTORY.value)
            return

        if mode == "mtproxy":
            await self._show_confirmation(app_user, plan, fulfillment_mode=OrderFulfillmentMode.MTPROXY.value)
            return

        if mode == "server":
            await self._notification_service.send_text(
                (
                    f"<b>{plan['name']}</b> 🖥\n\n"
                    "Выбирай конкретный сервер. Я оформлю заказ именно под него."
                ),
                user=app_user,
                keyboard=build_server_keyboard(plan_id, plan.get("hiddify_server_options", [])),
            )
            return

        if mode == "superkey":
            included_countries = sorted({server["country_name"] for server in plan.get("hiddify_server_options", [])})
            await self._show_confirmation(
                app_user,
                plan,
                fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SUPERKEY.value,
                included_countries=included_countries,
            )
            return

        await self._show_plan_entry(app_user, services, plan_id)

    async def _show_server_confirmation(self, app_user, services, plan_id: int, server_id: int) -> None:
        catalog = await self._svc(services, "plans").get_catalog()
        plan = _find_plan(catalog, plan_id)
        if plan is None:
            await self._show_catalog(app_user, services)
            return
        try:
            server = await self._svc(services, "hiddify").get_active_server_choice(server_id)
        except DomainError as exc:
            await self._notification_service.send_text(str(exc), user=app_user, keyboard=build_catalog_keyboard(catalog))
            return

        await self._show_confirmation(
            app_user,
            plan,
            fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SERVER.value,
            server_id=server.id,
            selected_server_name=server.name,
            selected_country_name=server.country_name,
        )

    async def _show_confirmation(
        self,
        app_user,
        plan: dict[str, Any],
        *,
        fulfillment_mode: str,
        server_id: int | None = None,
        selected_server_name: str | None = None,
        selected_country_name: str | None = None,
        included_countries: list[str] | None = None,
    ) -> None:
        await self._notification_service.send_text(
            _build_plan_confirmation_text(
                plan,
                fulfillment_mode=fulfillment_mode,
                selected_server_name=selected_server_name,
                selected_country_name=selected_country_name,
                included_countries=included_countries,
            ),
            user=app_user,
            keyboard=build_confirmation_keyboard(
                plan_id=int(plan["id"]),
                fulfillment_mode=fulfillment_mode,
                server_id=server_id,
            ),
        )

    async def _confirm_buy(
        self,
        app_user,
        services,
        *,
        plan_id: int,
        fulfillment_mode: str,
        server_id: int | None,
    ) -> None:
        catalog = await self._svc(services, "plans").get_catalog()
        plan = _find_plan(catalog, plan_id)
        if plan is None:
            await self._show_catalog(app_user, services)
            return

        selected_server_name = None
        selected_country_name = None
        included_countries: list[str] = []
        if fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SERVER.value and server_id:
            server = await self._svc(services, "hiddify").get_active_server_choice(server_id)
            selected_server_name = server.name
            selected_country_name = server.country_name
        elif fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SUPERKEY.value:
            included_countries = sorted({server["country_name"] for server in plan.get("hiddify_server_options", [])})

        try:
            order_id, payment_url = await self._svc(services, "orders").create_order_with_payment(
                user_id=app_user.id,
                plan_id=plan_id,
                requested_fulfillment_mode=fulfillment_mode,
                preferred_hiddify_server_id=server_id,
            )
        except DomainError as exc:
            await self._notification_service.send_text(
                str(exc),
                user=app_user,
                keyboard=build_catalog_keyboard(catalog),
            )
            return

        server_block = f"Сервер: {selected_server_name}\n" if selected_server_name else ""
        country_block = f"Страна: {selected_country_name}\n" if selected_country_name else ""
        superkey_block = (
            f"Страны суперключа: {', '.join(included_countries)}\n" if included_countries else ""
        )
        await self._notification_service.send_text(
            (
                "🎉 Готово, я создала заказ!\n\n"
                f"Номер заказа: #{order_id}\n"
                f"Формат: {_fulfillment_label(fulfillment_mode)}\n"
                f"{server_block}{country_block}{superkey_block}"
                "Что делать дальше:\n"
                "1. Нажми «Открыть оплату».\n"
                f"2. Вставь в сообщение к донату номер заказа: {order_id}\n"
                "3. После подтверждения оплаты я пришлю доступ сюда."
            ),
            user=app_user,
            keyboard=build_payment_keyboard(payment_url=payment_url),
        )
