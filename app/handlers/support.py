from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.keyboards.common import build_back_to_menu

router = Router()


async def _support_text(services) -> str:
    settings = services.notifications._settings
    support_line = "💬 Поддержка пока не настроена"
    support_username = settings.support_username
    support_url = settings.support_url
    shop_settings = getattr(services, "shop_settings", None)
    if shop_settings is None and isinstance(services, dict):
        shop_settings = services.get("shop_settings")
    if shop_settings is not None:
        support_username = await shop_settings.get_support_username()
        support_url = await shop_settings.get_support_url()
    if support_username:
        username = support_username.lstrip("@")
        support_line = f'💬 Поддержка: <a href="https://t.me/{username}">@{username}</a>'
    elif support_url:
        support_line = f"💬 Поддержка: {support_url}"

    content = getattr(services, "content", None)
    if content is None:
        content = getattr(services.notifications, "_content", None)
    if content is None:
        return (
            "<b>Юлия не справилась? 💔</b>\n\n"
            "Если оплата уже прошла, а ключ задержался, не переживай — мы спокойно всё проверим.\n"
            "Если нужен возврат или что-то выглядит странно, лучше сразу написать в поддержку.\n\n"
            f"{support_line}"
        )
    return content.get(
        "support.text",
        "<b>Юлия не справилась? 💔</b>\n\n"
        "Если оплата уже прошла, а ключ задержался, не переживай — мы спокойно всё проверим.\n"
        "Если нужен возврат или что-то выглядит странно, лучше сразу написать в поддержку.\n\n"
        "{support_line}",
        support_line=support_line,
    )


@router.message(Command("help"))
async def support_command(message: Message, services, **_: dict) -> None:
    await message.answer(await _support_text(services), reply_markup=build_back_to_menu())


@router.callback_query(F.data == "menu:support")
async def support_callback(callback: CallbackQuery, services, **_: dict) -> None:
    await callback.message.edit_text(await _support_text(services), reply_markup=build_back_to_menu())
    await callback.answer()
