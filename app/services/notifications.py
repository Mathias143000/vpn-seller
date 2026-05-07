from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Settings
from app.db.models import User, UserChannel
from app.services.content import ContentService
from app.services.vk_api import VKApiClient
from app.services.whatsapp_api import WhatsAppApiClient

GUIDE_PDF = Path(__file__).resolve().parents[2] / "assets" / "МОЙ-ПУТЕВОДИТЕЛЬ.pdf"


class NotificationService:
    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        vk_client: VKApiClient | None = None,
        whatsapp_client: WhatsAppApiClient | None = None,
        content: ContentService | None = None,
    ) -> None:
        self._bot = bot
        self._settings = settings
        self._vk_client = vk_client
        self._whatsapp_client = whatsapp_client
        self._content = content

    async def send_key_delivery(
        self,
        *,
        plan_name: str,
        key_value: str,
        expires_at: datetime | None,
        delivery_kind: str = "inventory",
        subscription_url: str | None = None,
        panel_url: str | None = None,
        deeplink_url: str | None = None,
        included_countries: list[str] | None = None,
        user: User | None = None,
        telegram_user_id: int | None = None,
    ) -> None:
        if delivery_kind in {"hiddify", "hiddify_superkey"}:
            title_key = "delivery.hiddify_superkey_title" if delivery_kind == "hiddify_superkey" else "delivery.hiddify_title"
            title_default = (
                "Готово! Я уже собрала твой суперключ."
                if delivery_kind == "hiddify_superkey"
                else "Готово! Я уже отправила твой доступ."
            )
            parts = [
                self._text(title_key, "🎉 <b>{title}</b>", title=title_default),
                self._text("delivery.plan_line", "Тариф: <b>{plan_name}</b>", plan_name=plan_name),
            ]
            if deeplink_url:
                parts.append(
                    self._text(
                        "delivery.hiddify_import_line",
                        'Импорт в Hiddify: <a href="{deeplink_url}">открыть в приложении</a>',
                        deeplink_url=deeplink_url,
                    )
                )
            if subscription_url:
                parts.append(
                    self._text(
                        "delivery.subscription_line",
                        "Ссылка-подписка: <code>{subscription_url}</code>",
                        subscription_url=subscription_url,
                    )
                )
            if included_countries:
                parts.append(
                    self._text(
                        "delivery.countries_line",
                        "Страны внутри: <b>{countries}</b>",
                        countries=", ".join(included_countries),
                    )
                )
            parts.append(
                self._text(
                    "delivery.hiddify_hint",
                    "Если импорт не открылся автоматически, скопируй ссылку-подписку и вставь её в VPN-клиент.",
                )
            )
        elif delivery_kind == "mtproxy":
            parts = [
                self._text("delivery.mtproxy_title", "🎉 <b>Готово! Я уже отправила твой MTProxy.</b>"),
                self._text("delivery.plan_line", "Тариф: <b>{plan_name}</b>", plan_name=plan_name),
                self._text("delivery.mtproxy_key_line", "MTProxy-ссылка: <code>{key_value}</code>", key_value=key_value),
                self._text(
                    "delivery.mtproxy_hint",
                    "Подключение: открой ссылку в Telegram или добавь её как MTProto proxy в настройках клиента.",
                ),
            ]
        else:
            parts = [
                self._text("delivery.inventory_title", "🎉 <b>Готово! Я уже отправила твой ключ.</b>"),
                self._text("delivery.plan_line", "Тариф: <b>{plan_name}</b>", plan_name=plan_name),
                self._text("delivery.key_line", "Твой ключ: <code>{key_value}</code>", key_value=key_value),
                self._text(
                    "delivery.inventory_hint",
                    "Подключение: открой ключ в VPN-клиенте и запусти соединение.",
                ),
            ]

        if expires_at is not None:
            parts.append(
                self._text(
                    "delivery.expires_line",
                    "Срок действия: {expires_at:%Y-%m-%d %H:%M UTC}",
                    expires_at=expires_at,
                )
            )
        if self._settings.support_username:
            parts.append(
                self._text(
                    "delivery.support_telegram_line",
                    "Поддержка: @{support_username}",
                    support_username=self._settings.support_username.lstrip("@"),
                )
            )
        elif self._settings.support_url:
            parts.append(
                self._text(
                    "delivery.support_url_line",
                    "Поддержка: {support_url}",
                    support_url=self._settings.support_url,
                )
            )

        await self.send_text(user=user, telegram_user_id=telegram_user_id, message="\n".join(parts))

    async def send_setup_guide(self, user: User | None = None, telegram_user_id: int | None = None) -> None:
        channel, target_id = self._resolve_destination(user=user, telegram_user_id=telegram_user_id)
        link_text = self._text(
            "delivery.setup_guide_link",
            "📘 Обязательно загляни в мой путеводитель.\n"
            "Там я собрала пошаговое подключение и ответы на частые вопросы.\n\n"
            "Скачать PDF: {setup_guide_url}",
            setup_guide_url=self._settings.setup_guide_url,
        )
        if channel == UserChannel.VK.value:
            await self._send_vk_message(int(target_id), link_text)
            return
        if channel == UserChannel.WHATSAPP.value:
            await self._send_whatsapp_message(str(target_id), link_text)
            return

        if not GUIDE_PDF.exists():
            return
        await self._bot.send_document(
            int(target_id),
            document=FSInputFile(GUIDE_PDF),
            caption=self._text(
                "delivery.setup_guide_caption",
                "📘 Обязательно загляни в мой путеводитель.\n"
                "Там я собрала пошаговое подключение и ответы на частые вопросы.",
            ),
        )

    async def send_admin_alert(self, message: str) -> None:
        for admin_id in self._settings.parsed_admin_ids:
            await self._bot.send_message(admin_id, f"[ALERT]\n{message}")

    async def send_admin_payment_review_request(
        self,
        *,
        order_id: int,
        plan_name: str,
        amount_value,
        amount_currency: str,
        payment_url: str,
        fulfillment_mode: str,
        preferred_server_name: str | None = None,
        preferred_country_name: str | None = None,
        customer_user: User | None = None,
        customer_telegram_user_id: int | None = None,
        customer_username: str | None = None,
    ) -> None:
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Подтвердить оплату", callback_data=f"admin:order:confirm:{order_id}", style="success")
        builder.button(text="❌ Отменить заказ", callback_data=f"admin:order:cancel:{order_id}", style="danger")
        builder.adjust(1)

        username_line = f"@{customer_username}" if customer_username else "без username"
        customer_label = self.describe_user(customer_user) if customer_user else f"telegram:{customer_telegram_user_id}"
        country_line = f"Страна сервера: {preferred_country_name}\n" if preferred_country_name else ""
        server_line = f"Сервер: {preferred_server_name}\n" if preferred_server_name else ""
        fulfillment_line = {
            "mtproxy": "MTProxy на наименее загруженном сервере",
            "inventory": "Готовый ключ со склада",
            "hiddify_server": "Конкретный Hiddify-сервер",
            "hiddify_superkey": "Суперключ по всем активным серверам",
        }.get(fulfillment_mode, fulfillment_mode)
        message = self._text(
            "admin.payment_review",
            "Новый заказ ждёт ручной проверки оплаты.\n"
            "Order #{order_id}\n"
            "Тариф: {plan_name}\n"
            "Сумма: {amount_value} {amount_currency}\n"
            "Режим выдачи: {fulfillment_line}\n"
            "{country_line}{server_line}"
            "Пользователь: {customer_label} ({username_line})\n"
            "Ссылка на оплату: {payment_url}\n\n"
            "Проверь донат в Donate.Stream и подтверди оплату кнопкой ниже.",
            order_id=order_id,
            plan_name=plan_name,
            amount_value=amount_value,
            amount_currency=amount_currency,
            fulfillment_line=fulfillment_line,
            country_line=country_line,
            server_line=server_line,
            customer_label=customer_label,
            username_line=username_line,
            payment_url=payment_url,
        )
        for admin_id in self._settings.parsed_admin_ids:
            await self._bot.send_message(admin_id, message, reply_markup=builder.as_markup())

    async def send_text(
        self,
        message: str,
        *,
        user: User | None = None,
        telegram_user_id: int | None = None,
        keyboard: dict[str, Any] | None = None,
    ) -> None:
        channel, target_id = self._resolve_destination(user=user, telegram_user_id=telegram_user_id)
        if channel == UserChannel.VK.value:
            await self._send_vk_message(int(target_id), self._to_plain_text(message), keyboard=keyboard)
            return
        if channel == UserChannel.WHATSAPP.value:
            await self._send_whatsapp_message(str(target_id), self._to_plain_text(message))
            return
        await self._bot.send_message(int(target_id), message)

    def describe_user(self, user: User | None) -> str:
        if user is None:
            return "unknown"
        if getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.WHATSAPP.value and user.whatsapp_phone:
            return f"whatsapp:{user.whatsapp_phone}"
        if getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.VK.value and user.vk_user_id:
            return f"vk:{user.vk_user_id}"
        return f"telegram:{user.telegram_user_id}"

    def _resolve_destination(self, *, user: User | None, telegram_user_id: int | None) -> tuple[str, int | str]:
        if user is not None:
            if (
                getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.WHATSAPP.value
                and user.whatsapp_phone
            ):
                return UserChannel.WHATSAPP.value, user.whatsapp_phone
            if getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.VK.value and user.vk_user_id:
                return UserChannel.VK.value, user.vk_user_id
            return UserChannel.TELEGRAM.value, user.telegram_user_id
        if telegram_user_id is not None:
            return UserChannel.TELEGRAM.value, telegram_user_id
        raise RuntimeError("Notification target is not specified.")

    async def _send_vk_message(self, peer_id: int, message: str, keyboard: dict[str, Any] | None = None) -> None:
        if self._vk_client is None:
            raise RuntimeError("VK integration is not configured.")
        await self._vk_client.send_message(peer_id=peer_id, message=message, keyboard=keyboard)

    async def _send_whatsapp_message(self, phone: str, message: str) -> None:
        if self._whatsapp_client is None:
            raise RuntimeError("WhatsApp integration is not configured.")
        await self._whatsapp_client.send_text_message(to=phone, body=message, preview_url=True)

    def _text(self, key: str, default: str, **values: Any) -> str:
        if self._content is None:
            return default.format(**values)
        return self._content.get(key, default, **values)

    @staticmethod
    def _to_plain_text(message: str) -> str:
        text = re.sub(
            r'<a\s+href="([^"]+)">([^<]+)</a>',
            lambda match: f"{match.group(2)} ({match.group(1)})",
            message,
        )
        text = re.sub(r"</?(b|code|i|u|pre)>", "", text)
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text)
