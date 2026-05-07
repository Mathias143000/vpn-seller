from __future__ import annotations

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
from app.services.whatsapp_api import WhatsAppApiClient
from app.services.whatsapp_ui import (
    build_catalog_sections,
    build_confirmation_buttons,
    build_fulfillment_buttons,
    build_main_menu_buttons,
    build_server_sections,
)


class WhatsAppBotService:
    def __init__(
        self,
        settings: Settings,
        whatsapp_client: WhatsAppApiClient,
        notification_service: NotificationService,
    ) -> None:
        self._settings = settings
        self._whatsapp_client = whatsapp_client
        self._notification_service = notification_service

    @staticmethod
    def _svc(services, name: str):
        if isinstance(services, dict):
            if name in services:
                return services[name]
            if name == "notifications" and "notification" in services:
                return services["notification"]
            raise KeyError(name)
        return getattr(services, name)

    async def handle_webhook(self, payload: dict[str, Any], services) -> None:
        if payload.get("object") != "whatsapp_business_account":
            return

        for entry in payload.get("entry", []):
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes", []):
                if not isinstance(change, dict):
                    continue
                value = change.get("value") or {}
                contacts = value.get("contacts") or []
                messages = value.get("messages") or []
                if not messages:
                    continue
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    user = await self._ensure_whatsapp_user(services, contacts, message)
                    command = self._parse_command(message)
                    await self._dispatch_command(command, user, services)

    async def _ensure_whatsapp_user(self, services, contacts: list[dict[str, Any]], message: dict[str, Any]):
        phone = str(message.get("from") or "").strip()
        if not phone:
            raise RuntimeError("WhatsApp sender is missing.")

        contact = contacts[0] if contacts else {}
        profile = contact.get("profile") if isinstance(contact, dict) else {}
        full_name = profile.get("name") if isinstance(profile, dict) else None
        username = None

        session = self._svc(services, "session")
        users_service = self._svc(services, "users")
        async with transactional(session):
            return await users_service.ensure_from_whatsapp(
                whatsapp_phone=phone,
                username=username,
                full_name=full_name or f"WhatsApp {phone}",
            )

    def _parse_command(self, message: dict[str, Any]) -> str:
        interactive = message.get("interactive")
        if isinstance(interactive, dict):
            button_reply = interactive.get("button_reply")
            if isinstance(button_reply, dict) and button_reply.get("id"):
                return str(button_reply["id"])
            list_reply = interactive.get("list_reply")
            if isinstance(list_reply, dict) and list_reply.get("id"):
                return str(list_reply["id"])

        text = ""
        if isinstance(message.get("text"), dict):
            text = str(message["text"].get("body") or "").strip().lower()
        elif message.get("text"):
            text = str(message.get("text") or "").strip().lower()

        mapping = {
            "/start": "menu_root",
            "start": "menu_root",
            "menu": "menu_root",
            "меню": "menu_root",
            "catalog": "menu_catalog",
            "каталог": "menu_catalog",
            "тарифы": "menu_catalog",
            "orders": "menu_orders",
            "покупки": "menu_orders",
            "мои покупки": "menu_orders",
            "support": "menu_support",
            "поддержка": "menu_support",
        }
        if text.startswith("buy "):
            return f"buy_code:{text.split(maxsplit=1)[1]}"
        if text.startswith("/promo ") or text.startswith("promo "):
            return f"promo_set:{text.split(maxsplit=1)[1]}"
        return mapping.get(text, "menu_root")

    async def _dispatch_command(self, command: str, app_user, services) -> None:
        if command == "menu_root":
            await self._show_welcome(app_user)
            return
        if command == "menu_catalog":
            await self._show_catalog(app_user, services)
            return
        if command == "menu_orders":
            await self._show_orders(app_user, services)
            return
        if command == "menu_support":
            await self._show_support(app_user)
            return
        if command.startswith("promo_set:"):
            await self._set_promo(app_user, services, command.split(":", maxsplit=1)[1])
            return
        if command.startswith("buy_plan:"):
            await self._show_plan_entry(app_user, services, int(command.split(":")[1]))
            return
        if command.startswith("buy_code:"):
            await self._show_plan_by_code(app_user, services, command.split(":", maxsplit=1)[1])
            return
        if command.startswith("buy_mode:"):
            _, plan_id, mode = command.split(":", maxsplit=2)
            await self._show_buy_mode(app_user, services, int(plan_id), mode)
            return
        if command.startswith("buy_server:"):
            _, plan_id, server_id = command.split(":", maxsplit=2)
            await self._show_server_confirmation(app_user, services, int(plan_id), int(server_id))
            return
        if command.startswith("confirm_buy:"):
            parts = command.split(":")
            plan_id = int(parts[1])
            mode = parts[2]
            server_id = int(parts[3]) if len(parts) > 3 else None
            await self._confirm_buy(app_user, services, plan_id=plan_id, fulfillment_mode=mode, server_id=server_id)
            return
        await self._show_welcome(app_user)

    async def _set_promo(self, app_user, services, code: str) -> None:
        try:
            preview = await self._svc(services, "promos").set_active_for_user(user_id=app_user.id, code=code)
        except DomainError as exc:
            await self._notification_service.send_text(f"Не смогла применить промокод: {exc}", user=app_user)
            return
        await self._notification_service.send_text(
            f"Промокод {preview.code} активирован. Я применю его к следующему заказу, если он подходит по условиям.",
            user=app_user,
        )

    async def _show_welcome(self, app_user) -> None:
        await self._whatsapp_client.send_buttons(
            to=app_user.whatsapp_phone,
            body=NotificationService._to_plain_text(_welcome_text()),
            buttons=build_main_menu_buttons(),
        )

    async def _show_catalog(self, app_user, services) -> None:
        catalog = await self._svc(services, "plans").get_catalog()
        content = self._svc(services, "content") if isinstance(services, dict) and "content" in services else getattr(services, "content", None)
        await self._whatsapp_client.send_list(
            to=app_user.whatsapp_phone,
            header_text="Тарифы Юлии",
            body=NotificationService._to_plain_text(_format_catalog(catalog, content)),
            button_text="Выбрать",
            sections=build_catalog_sections(catalog),
        )

    async def _show_orders(self, app_user, services) -> None:
        orders = await self._svc(services, "orders").list_user_orders(app_user.id)
        await self._notification_service.send_text(
            NotificationService._to_plain_text(_format_orders(orders)),
            user=app_user,
        )
        await self._whatsapp_client.send_buttons(
            to=app_user.whatsapp_phone,
            body="Если хочешь, могу сразу вернуть тебя в меню.",
            buttons=build_main_menu_buttons(),
        )

    async def _show_support(self, app_user) -> None:
        await self._notification_service.send_text(
            NotificationService._to_plain_text(
                await _support_text(SimpleNamespace(notifications=self._notification_service))
            ),
            user=app_user,
        )
        await self._whatsapp_client.send_buttons(
            to=app_user.whatsapp_phone,
            body="Я рядом. Если хочешь, могу показать каталог или покупки.",
            buttons=build_main_menu_buttons(),
        )

    async def _show_plan_by_code(self, app_user, services, plan_code: str) -> None:
        catalog = await self._svc(services, "plans").get_catalog()
        plan = next((item for item in catalog if str(item["code"]).lower() == plan_code.lower()), None)
        if plan is None:
            await self._notification_service.send_text(
                "Не смогла найти такой тариф. Открой каталог и выбери его там.",
                user=app_user,
            )
            await self._show_catalog(app_user, services)
            return
        await self._show_plan_entry(app_user, services, int(plan["id"]))

    async def _show_plan_entry(self, app_user, services, plan_id: int) -> None:
        catalog = await self._svc(services, "plans").get_catalog()
        plan = _find_plan(catalog, plan_id)
        if plan is None:
            await self._notification_service.send_text("Не смогла найти этот тариф.", user=app_user)
            return
        if not plan["is_available"]:
            await self._notification_service.send_text(_plan_unavailable_message(plan), user=app_user)
            await self._show_catalog(app_user, services)
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

        await self._whatsapp_client.send_buttons(
            to=app_user.whatsapp_phone,
            body=NotificationService._to_plain_text(_build_fulfillment_prompt(plan)),
            buttons=build_fulfillment_buttons(plan),
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
            await self._whatsapp_client.send_list(
                to=app_user.whatsapp_phone,
                header_text=plan["name"],
                body="Выбирай конкретный сервер. Я оформлю заказ именно под него.",
                button_text="Серверы",
                sections=build_server_sections(plan_id, plan.get("hiddify_server_options", [])),
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
            await self._notification_service.send_text(str(exc), user=app_user)
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
        await self._whatsapp_client.send_buttons(
            to=app_user.whatsapp_phone,
            body=NotificationService._to_plain_text(
                _build_plan_confirmation_text(
                    plan,
                    fulfillment_mode=fulfillment_mode,
                    selected_server_name=selected_server_name,
                    selected_country_name=selected_country_name,
                    included_countries=included_countries,
                )
            ),
            buttons=build_confirmation_buttons(plan_id=int(plan["id"]), fulfillment_mode=fulfillment_mode, server_id=server_id),
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
            await self._notification_service.send_text(str(exc), user=app_user)
            return

        server_block = f"Сервер: {selected_server_name}\n" if selected_server_name else ""
        country_block = f"Страна: {selected_country_name}\n" if selected_country_name else ""
        superkey_block = f"Страны суперключа: {', '.join(included_countries)}\n" if included_countries else ""
        message = (
            "Готово, я создала заказ.\n\n"
            f"Номер заказа: #{order_id}\n"
            f"Формат: {_fulfillment_label(fulfillment_mode)}\n"
            f"{server_block}{country_block}{superkey_block}"
            "Что делать дальше:\n"
            f"1. Перейди по ссылке на оплату: {payment_url}\n"
            f"2. В комментарий к донату вставь номер заказа: {order_id}\n"
            "3. После подтверждения оплаты я пришлю доступ прямо сюда."
        )
        await self._notification_service.send_text(message, user=app_user)
        await self._whatsapp_client.send_buttons(
            to=app_user.whatsapp_phone,
            body="Если захочешь, я могу показать каталог или твои покупки.",
            buttons=build_main_menu_buttons(),
        )
