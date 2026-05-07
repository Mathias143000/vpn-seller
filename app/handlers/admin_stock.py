from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.keyboards.common import build_back_navigation
from app.services.exceptions import AccessDeniedError
from app.services.users import UsersService

router = Router()


def _format_stock(rows: list[dict]) -> str:
    lines = ["<b>Остатки по тарифам 📦</b>", ""]
    for row in rows:
        plan = row["plan"]
        lines.append(
            f"🧾 <b>{plan.name}</b>\n"
            f"Свободно: <b>{row['available_count']}</b>\n"
            f"В резерве: {row['reserved_count']}\n"
            f"Выдано: {row['issued_count']}\n"
            f"Проблемных: {row['broken_count']}"
        )
        lines.append("")
    return "\n".join(lines).strip()


async def _render_stock(target, app_user, services):
    UsersService.require_admin(app_user)
    rows = await services.inventory.get_inventory_summary()
    text = _format_stock(rows)
    markup = build_back_navigation(back_callback="menu:admin", back_text="⬅️ В админку")
    if hasattr(target, "edit_text"):
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(Command("admin_stock"))
async def admin_stock_command(message: Message, app_user, services, **_: dict) -> None:
    try:
        await _render_stock(message, app_user, services)
    except AccessDeniedError:
        await message.answer("Кажется, у вас пока нет доступа к админке 🙂")


@router.callback_query(F.data == "admin:stock")
async def admin_stock_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    try:
        await _render_stock(callback.message, app_user, services)
        await callback.answer()
    except AccessDeniedError:
        await callback.answer("Кажется, вы еще не админ 🙂", show_alert=True)
