from __future__ import annotations

from aiogram.filters import Command
from aiogram.types import Message
from aiogram import Router

from app.services.exceptions import AccessDeniedError
from app.services.shop_settings import ShopSettingsService
from app.services.users import UsersService

router = Router()


HELP_TEXT = (
    "<b>Настройки магазина</b>\n\n"
    "Посмотреть: <code>/admin_settings</code>\n"
    "Donate.Stream URL: <code>/admin_settings donate_url https://...</code>\n"
    "Telegram support: <code>/admin_settings support_username username</code>\n"
    "Support URL: <code>/admin_settings support_url https://...</code>\n"
    "Очистить значение: <code>/admin_settings key -</code>\n\n"
    "Поддерживаемые key: <code>donate_url</code>, <code>support_username</code>, <code>support_url</code>"
)


KEY_ALIASES = {
    "donate_url": ShopSettingsService.DONATE_STREAM_URL,
    "donate_stream_url": ShopSettingsService.DONATE_STREAM_URL,
    "support_username": ShopSettingsService.SUPPORT_USERNAME,
    "support_url": ShopSettingsService.SUPPORT_URL,
}


@router.message(Command("admin_settings"))
async def admin_settings_command(message: Message, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Нет доступа к админке.")
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) == 1:
        summary = await services.shop_settings.summary()
        await message.answer(
            "<b>Текущие настройки магазина</b>\n\n"
            f"Donate.Stream URL: <code>{summary[ShopSettingsService.DONATE_STREAM_URL] or 'не задан'}</code>\n"
            f"Support username: <code>{summary[ShopSettingsService.SUPPORT_USERNAME] or 'не задан'}</code>\n"
            f"Support URL: <code>{summary[ShopSettingsService.SUPPORT_URL] or 'не задан'}</code>\n\n"
            f"{HELP_TEXT}"
        )
        return

    if len(parts) < 3:
        await message.answer(HELP_TEXT)
        return

    raw_key = parts[1].strip().lower()
    key = KEY_ALIASES.get(raw_key)
    if key is None:
        await message.answer(HELP_TEXT)
        return

    value = parts[2].strip()
    if value == "-":
        value = None
    if key == ShopSettingsService.SUPPORT_USERNAME and isinstance(value, str):
        value = value.lstrip("@")

    await services.shop_settings.set_value(key=key, value=value, actor_user_id=app_user.id)
    await message.answer("Настройка сохранена.")
