from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.keyboards.admin import build_admin_menu
from app.keyboards.common import build_main_menu
from app.services.exceptions import AccessDeniedError
from app.services.users import UsersService

router = Router()

HELLO_IMAGE = Path(__file__).resolve().parents[2] / "assets" / "hello.png"


def _hello_caption() -> str:
    return (
        "<b>Привет! Я Юлия 👋</b>\n\n"
        "Я помогу тебе выбрать тариф, оформить заказ и получить VPN-доступ без лишней суеты 🔐"
    )


def _welcome_text() -> str:
    return (
        "<b>Я уже рядом ✨</b>\n\n"
        "Выбирай, что хочешь сделать:\n"
        "• посмотреть тарифы;\n"
        "• открыть свои заказы;\n"
        "• написать в поддержку.\n\n"
        "Если что-то пойдет не так, я мягко подскажу, куда нажать дальше 💛"
    )


@router.message(CommandStart())
async def start_handler(message: Message, app_user, services, **_: dict) -> None:
    await message.answer_photo(
        FSInputFile(HELLO_IMAGE),
        caption=services.content.get("start.hello_caption", _hello_caption()),
    )
    await message.answer(
        services.content.get("start.welcome_text", _welcome_text()),
        reply_markup=build_main_menu(is_admin=UsersService.is_admin(app_user)),
    )


@router.callback_query(F.data == "menu:root")
async def root_menu_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    await callback.message.edit_text(
        services.content.get("start.welcome_text", _welcome_text()),
        reply_markup=build_main_menu(is_admin=UsersService.is_admin(app_user)),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:admin")
async def admin_menu_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_admin(app_user)
    except AccessDeniedError:
        await callback.answer(services.content.get("start.admin_denied", "Кажется, ты еще не админ :)"), show_alert=True)
        return
    await callback.message.edit_text(
        services.content.get("start.admin_title", "<b>Админка Юлии 🛠</b>\n\nВыбирай нужное действие ниже."),
        reply_markup=build_admin_menu(),
    )
    await callback.answer()
