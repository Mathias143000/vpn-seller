from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.db.models import PromoDiscountType
from app.keyboards.common import build_back_navigation
from app.services.exceptions import AccessDeniedError, DomainError
from app.services.users import UsersService

router = Router()


def _usage_text() -> str:
    return (
        "<b>Промокоды 🎟</b>\n\n"
        "Создать или обновить:\n"
        "<code>/admin_promo JULIA10 percent 10</code>\n"
        "<code>/admin_promo START fixed 100 50</code>\n\n"
        "Формат:\n"
        "<code>/admin_promo CODE percent|fixed value [max_uses]</code>\n\n"
        "percent — скидка в процентах, максимум 95.\n"
        "fixed — фиксированная скидка в рублях.\n"
        "max_uses можно не указывать."
    )


def _format_promos(promos) -> str:
    if not promos:
        return _usage_text() + "\n\nПока промокодов нет."
    lines = [_usage_text(), "", "<b>Активные и сохранённые промокоды:</b>"]
    for promo in promos:
        value = f"{promo.discount_value}%"
        if promo.discount_type == PromoDiscountType.FIXED.value:
            value = f"{promo.discount_value} RUB"
        limit = "без лимита" if promo.max_uses is None else f"{promo.used_count}/{promo.max_uses}"
        status = "активен" if promo.is_active else "выключен"
        lines.append(f"• <code>{promo.code}</code> — {value}, {limit}, {status}")
    return "\n".join(lines)


async def _show_promos(target, services) -> None:
    promos = await services.promos.list_codes()
    markup = build_back_navigation(back_callback="menu:admin", back_text="⬅️ В админку")
    if hasattr(target, "edit_text"):
        await target.edit_text(_format_promos(promos), reply_markup=markup)
    else:
        await target.answer(_format_promos(promos), reply_markup=markup)


@router.message(Command("admin_promo"))
async def admin_promo_command(message: Message, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_admin(app_user)
    except AccessDeniedError:
        await message.answer("Кажется, у тебя пока нет доступа к админке 🙂")
        return

    parts = (message.text or "").split()
    if len(parts) == 1:
        await _show_promos(message, services)
        return
    if len(parts) < 4:
        await message.answer(_usage_text())
        return

    code = parts[1]
    discount_type = parts[2].lower()
    try:
        discount_value = Decimal(parts[3].replace(",", "."))
    except (InvalidOperation, ValueError):
        await message.answer("Сумма или процент скидки должны быть числом.")
        return
    try:
        max_uses = int(parts[4]) if len(parts) >= 5 else None
    except ValueError:
        await message.answer("Лимит использований должен быть целым числом.")
        return

    try:
        promo = await services.promos.create_or_update(
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            max_uses=max_uses,
            actor_user_id=app_user.id,
        )
    except DomainError as exc:
        await message.answer(f"Не смогла сохранить промокод: {exc}")
        return

    await message.answer(
        f"Промокод <code>{promo.code}</code> сохранён ✨\n"
        f"Тип: <b>{promo.discount_type}</b>\n"
        f"Значение: <b>{promo.discount_value}</b>\n"
        f"Лимит: <b>{promo.max_uses if promo.max_uses is not None else 'без лимита'}</b>"
    )


@router.callback_query(F.data == "admin:promos")
async def admin_promos_callback(callback: CallbackQuery, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_admin(app_user)
        await _show_promos(callback.message, services)
        await callback.answer()
    except AccessDeniedError:
        await callback.answer("Кажется, ты еще не админ 🙂", show_alert=True)
