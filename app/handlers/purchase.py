from __future__ import annotations

from decimal import Decimal
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.db.models import OrderFulfillmentMode, PlanProvisioningMode
from app.keyboards.catalog import build_fulfillment_selection, build_purchase_confirmation, build_server_selection
from app.keyboards.common import build_payment_actions
from app.services.exceptions import DomainError, InvalidStateError, NotFoundError, OutOfStockError
from app.states.purchase import PurchaseState

router = Router()

BACK_TEXT = "⬅️ Назад"


def _find_plan(catalog: list[dict], plan_id: int) -> dict | None:
    return next((item for item in catalog if item["id"] == plan_id), None)


def _content(services, key: str, default: str, **values) -> str:
    content = getattr(services, "content", None)
    if content is None and isinstance(services, dict):
        content = services.get("content")
    if content is None:
        try:
            return default.format(**values)
        except Exception:
            return default
    return content.get(key, default, **values)


def _fmt_money(value: Any) -> str:
    amount = Decimal(str(value))
    if amount == amount.to_integral():
        return str(int(amount))
    return f"{amount:.2f}"


def _plan_mode_price(plan: dict, fulfillment_mode: str | None) -> Decimal:
    key_by_mode = {
        OrderFulfillmentMode.INVENTORY.value: "inventory_price_value",
        OrderFulfillmentMode.MTPROXY.value: "mtproxy_price_value",
        OrderFulfillmentMode.HIDDIFY_SERVER.value: "server_price_value",
        OrderFulfillmentMode.HIDDIFY_SUPERKEY.value: "superkey_price_value",
    }
    key = key_by_mode.get(fulfillment_mode or OrderFulfillmentMode.INVENTORY.value, "price_value")
    return Decimal(str(plan.get(key) or plan["price_value"]))


def _promo_dict(preview) -> dict[str, str] | None:
    if preview is None:
        return None
    return {
        "promo_code": preview.code,
        "discount_amount": _fmt_money(preview.discount_amount),
        "final_amount": _fmt_money(preview.final_amount),
        "original_amount": _fmt_money(preview.original_amount),
        "discount_label": preview.discount_label,
    }


def _plan_unavailable_message(plan: dict) -> str:
    if plan["provisioning_mode"] == PlanProvisioningMode.MTPROXY.value:
        return "Сейчас нет активных серверов, на которых можно выдать MTProxy 🙏"
    if plan["provisioning_mode"] == PlanProvisioningMode.INVENTORY.value:
        return "Для этого тарифа закончились подготовленные ключи 🙏"
    if plan["provisioning_mode"] == PlanProvisioningMode.HIDDIFY.value:
        return "Для этого тарифа пока не подключён ни один активный сервер 🙏"
    return "Сейчас нет ни подготовленных ключей, ни доступных серверов для выдачи 🙏"


def _fulfillment_label(fulfillment_mode: str) -> str:
    return {
        OrderFulfillmentMode.MTPROXY.value: "⚡ MTProxy на наименее загруженном сервере",
        OrderFulfillmentMode.INVENTORY.value: "🔑 Готовый ключ со склада",
        OrderFulfillmentMode.HIDDIFY_SERVER.value: "🖥 Доступ с выбранного сервера",
        OrderFulfillmentMode.HIDDIFY_SUPERKEY.value: "🌐 Суперключ по всем активным серверам",
    }.get(fulfillment_mode, fulfillment_mode)


def _build_plan_confirmation_text(
    plan: dict,
    *,
    fulfillment_mode: str,
    selected_server_name: str | None = None,
    selected_country_name: str | None = None,
    included_countries: list[str] | None = None,
    promo_preview: dict[str, str] | None = None,
) -> str:
    price = _plan_mode_price(plan, fulfillment_mode)
    details: list[str] = [
        f"<b>{plan['name']}</b> ✨",
        "",
        f"Срок: <b>{plan['duration_days']} дней</b>",
        f"Стоимость: <b>{_fmt_money(price)} {plan['price_currency']}</b>",
        f"Формат: <b>{_fulfillment_label(fulfillment_mode)}</b>",
    ]
    if selected_server_name:
        details.append(f"Сервер: <b>{selected_server_name}</b>")
    if selected_country_name:
        details.append(f"Страна: <b>{selected_country_name}</b>")
    if included_countries:
        details.append(f"Подключу страны: <b>{', '.join(included_countries)}</b>")
    if promo_preview:
        details.extend(
            [
                "",
                f"Промокод: <b>{promo_preview['promo_code']}</b>",
                f"Скидка: <b>{promo_preview['discount_amount']} {plan['price_currency']}</b>",
                f"Итого: <b>{promo_preview['final_amount']} {plan['price_currency']}</b>",
            ]
        )
    details.extend(
        [
            "",
            "Если всё подходит, я сейчас оформлю заказ и дам тебе ссылку на оплату.",
            "Промокод можно ввести перед оформлением, кнопка ниже.",
        ]
    )
    return "\n".join(details)


def _build_fulfillment_prompt(plan: dict) -> str:
    parts = [
        f"<b>{plan['name']}</b> ✨",
        "",
        "Выбирай, как тебе удобнее получить доступ:",
    ]
    if plan.get("inventory_available"):
        price = _plan_mode_price(plan, OrderFulfillmentMode.INVENTORY.value)
        parts.append(f"🔑 Готовый ключ со склада — {_fmt_money(price)} {plan['price_currency']}")
    if plan.get("mtproxy_available"):
        price = _plan_mode_price(plan, OrderFulfillmentMode.MTPROXY.value)
        parts.append(f"⚡ MTProxy на наименее загруженном сервере — {_fmt_money(price)} {plan['price_currency']}")
    if plan.get("hiddify_server_options"):
        price = _plan_mode_price(plan, OrderFulfillmentMode.HIDDIFY_SERVER.value)
        parts.append(f"🖥 Выбор конкретного сервера — {_fmt_money(price)} {plan['price_currency']}")
    if plan.get("superkey_available"):
        price = _plan_mode_price(plan, OrderFulfillmentMode.HIDDIFY_SUPERKEY.value)
        countries = sorted({server["country_name"] for server in plan["hiddify_server_options"]})
        parts.append(
            f"🌐 Суперключ по странам: {', '.join(countries)} — {_fmt_money(price)} {plan['price_currency']}"
        )
    return "\n".join(parts)


def _confirmation_kwargs_from_state(data: dict) -> dict:
    return {
        "fulfillment_mode": data.get("fulfillment_mode") or OrderFulfillmentMode.INVENTORY.value,
        "selected_server_name": data.get("selected_server_name"),
        "selected_country_name": data.get("selected_country_name"),
        "included_countries": data.get("included_countries") or None,
        "promo_preview": data.get("promo_preview"),
    }


def _promo_block_from_order(order) -> str:
    if order is None or not getattr(order, "promo_code", None):
        return ""
    discount = _fmt_money(getattr(order, "discount_amount_value", 0) or 0)
    return f"Промокод: <b>{order.promo_code}</b>, скидка <b>{discount} {order.amount_currency}</b>\n"


async def _send_confirmation(callback: CallbackQuery, state: FSMContext, plan: dict, *, back_callback: str) -> None:
    data = await state.get_data()
    await callback.message.edit_text(
        _build_plan_confirmation_text(plan, **_confirmation_kwargs_from_state(data)),
        reply_markup=build_purchase_confirmation(plan["id"], back_callback=back_callback, back_text=BACK_TEXT),
    )


@router.message(Command("promo"))
async def promo_command(message: Message, app_user, services, **_: dict) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(_content(services, "purchase.promo_prompt", "Введи промокод: <code>/promo JULIA10</code>"))
        return
    try:
        preview = await services.promos.set_active_for_user(user_id=app_user.id, code=parts[1].strip())
    except DomainError as exc:
        await message.answer(
            _content(services, "purchase.promo_invalid", "Не смогла применить промокод: {reason}", reason=str(exc))
        )
        return
    await message.answer(
        f"Промокод <b>{preview.code}</b> активирован ✨\n"
        "Я применю его к следующему заказу, если он подходит по условиям."
    )


@router.callback_query(F.data.startswith("buy:"))
async def buy_callback(callback: CallbackQuery, state: FSMContext, services, **_: dict) -> None:
    plan_id = int(callback.data.split(":")[1])
    catalog = await services.plans.get_catalog()
    plan = _find_plan(catalog, plan_id)
    if plan is None:
        await callback.answer("Не смогла найти этот тариф 😕", show_alert=True)
        return
    if not plan["is_available"]:
        await callback.answer(_plan_unavailable_message(plan), show_alert=True)
        return

    await state.clear()
    await state.update_data(plan_id=plan_id)
    await state.set_state(PurchaseState.selecting_fulfillment)

    inventory_available = bool(plan.get("inventory_available"))
    mtproxy_available = bool(plan.get("mtproxy_available"))
    server_options = plan.get("hiddify_server_options", [])
    superkey_available = bool(plan.get("superkey_available"))

    if (inventory_available or mtproxy_available) and not server_options and not superkey_available:
        fulfillment_mode = (
            OrderFulfillmentMode.MTPROXY.value if mtproxy_available else OrderFulfillmentMode.INVENTORY.value
        )
        await state.update_data(fulfillment_mode=fulfillment_mode)
        await state.set_state(PurchaseState.confirming_plan)
        await callback.message.edit_text(
            _build_plan_confirmation_text(plan, fulfillment_mode=fulfillment_mode),
            reply_markup=build_purchase_confirmation(plan_id, back_callback=f"buy:{plan_id}", back_text=BACK_TEXT),
        )
        await callback.answer()
        return

    if not inventory_available and not mtproxy_available and len(server_options) == 1 and not superkey_available:
        server = server_options[0]
        await state.update_data(
            fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SERVER.value,
            preferred_hiddify_server_id=server["server_id"],
            selected_server_name=server["server_name"],
            selected_country_name=server["country_name"],
        )
        await state.set_state(PurchaseState.confirming_plan)
        await callback.message.edit_text(
            _build_plan_confirmation_text(
                plan,
                fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SERVER.value,
                selected_server_name=server["server_name"],
                selected_country_name=server["country_name"],
            ),
            reply_markup=build_purchase_confirmation(plan_id, back_callback=f"buy:{plan_id}", back_text=BACK_TEXT),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        _build_fulfillment_prompt(plan),
        reply_markup=build_fulfillment_selection(plan_id, plan),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy_mode:"))
async def buy_mode_callback(callback: CallbackQuery, state: FSMContext, services, **_: dict) -> None:
    _, plan_id_raw, mode = callback.data.split(":")
    plan_id = int(plan_id_raw)
    catalog = await services.plans.get_catalog()
    plan = _find_plan(catalog, plan_id)
    if plan is None:
        await callback.answer("Не смогла найти этот тариф 😕", show_alert=True)
        return

    if mode == OrderFulfillmentMode.INVENTORY.value:
        await state.update_data(
            plan_id=plan_id,
            fulfillment_mode=OrderFulfillmentMode.INVENTORY.value,
            preferred_hiddify_server_id=None,
            selected_server_name=None,
            selected_country_name=None,
            included_countries=None,
            promo_code=None,
            promo_preview=None,
        )
        await state.set_state(PurchaseState.confirming_plan)
        await callback.message.edit_text(
            _build_plan_confirmation_text(plan, fulfillment_mode=OrderFulfillmentMode.INVENTORY.value),
            reply_markup=build_purchase_confirmation(plan_id, back_callback=f"buy:{plan_id}", back_text=BACK_TEXT),
        )
        await callback.answer("Выбрала готовый ключ ✨")
        return

    if mode == OrderFulfillmentMode.MTPROXY.value:
        await state.update_data(
            plan_id=plan_id,
            fulfillment_mode=OrderFulfillmentMode.MTPROXY.value,
            preferred_hiddify_server_id=None,
            selected_server_name=None,
            selected_country_name=None,
            included_countries=None,
            promo_code=None,
            promo_preview=None,
        )
        await state.set_state(PurchaseState.confirming_plan)
        await callback.message.edit_text(
            _build_plan_confirmation_text(plan, fulfillment_mode=OrderFulfillmentMode.MTPROXY.value),
            reply_markup=build_purchase_confirmation(plan_id, back_callback=f"buy:{plan_id}", back_text=BACK_TEXT),
        )
        await callback.answer("Выбрала MTProxy ⚡")
        return

    if mode == "server":
        await state.update_data(plan_id=plan_id, promo_code=None, promo_preview=None)
        await state.set_state(PurchaseState.selecting_server)
        await callback.message.edit_text(
            f"<b>{plan['name']}</b> 🖥\n\nВыбирай конкретный сервер. Я оформлю заказ именно под него.",
            reply_markup=build_server_selection(plan_id, plan.get("hiddify_server_options", [])),
        )
        await callback.answer()
        return

    if mode == "superkey":
        included_countries = sorted({server["country_name"] for server in plan.get("hiddify_server_options", [])})
        await state.update_data(
            plan_id=plan_id,
            fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SUPERKEY.value,
            preferred_hiddify_server_id=None,
            selected_server_name="Суперключ",
            selected_country_name=None,
            included_countries=included_countries,
            promo_code=None,
            promo_preview=None,
        )
        await state.set_state(PurchaseState.confirming_plan)
        await callback.message.edit_text(
            _build_plan_confirmation_text(
                plan,
                fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SUPERKEY.value,
                included_countries=included_countries,
            ),
            reply_markup=build_purchase_confirmation(plan_id, back_callback=f"buy:{plan_id}", back_text=BACK_TEXT),
        )
        await callback.answer("Выбрала суперключ 🌐")
        return

    await callback.answer("Не поняла формат покупки 😕", show_alert=True)


@router.callback_query(F.data.startswith("buy_server:"))
async def buy_server_callback(callback: CallbackQuery, state: FSMContext, services, **_: dict) -> None:
    _, plan_id_raw, server_id_raw = callback.data.split(":")
    plan_id = int(plan_id_raw)
    server_id = int(server_id_raw)

    catalog = await services.plans.get_catalog()
    plan = _find_plan(catalog, plan_id)
    if plan is None:
        await callback.answer("Не смогла найти этот тариф 😕", show_alert=True)
        return

    try:
        server = await services.hiddify.get_active_server_choice(server_id)
    except NotFoundError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        plan_id=plan_id,
        fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SERVER.value,
        preferred_hiddify_server_id=server.id,
        selected_server_name=server.name,
        selected_country_name=server.country_name,
        included_countries=None,
        promo_code=None,
        promo_preview=None,
    )
    await state.set_state(PurchaseState.confirming_plan)
    await callback.message.edit_text(
        _build_plan_confirmation_text(
            plan,
            fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SERVER.value,
            selected_server_name=server.name,
            selected_country_name=server.country_name,
        ),
        reply_markup=build_purchase_confirmation(plan_id, back_callback=f"buy:{plan_id}", back_text=BACK_TEXT),
    )
    await callback.answer(f"Выбрала сервер: {server.name}")


@router.callback_query(F.data.startswith("promo:ask:"))
async def ask_promo_callback(callback: CallbackQuery, state: FSMContext, services, **_: dict) -> None:
    plan_id = int(callback.data.split(":")[2])
    data = await state.get_data()
    if not data.get("fulfillment_mode"):
        await callback.answer("Сначала выбери формат выдачи, а потом промокод 🙏", show_alert=True)
        return
    await state.update_data(plan_id=plan_id)
    await state.set_state(PurchaseState.entering_promo)
    await callback.message.answer(
        _content(
            services,
            "purchase.promo_prompt",
            "Введи промокод одним сообщением. Например: <code>JULIA10</code>",
        )
    )
    await callback.answer()


@router.message(PurchaseState.entering_promo)
async def promo_input_handler(message: Message, state: FSMContext, app_user, services, **_: dict) -> None:
    code = (message.text or "").strip()
    data = await state.get_data()
    plan_id = int(data.get("plan_id") or 0)
    catalog = await services.plans.get_catalog()
    plan = _find_plan(catalog, plan_id)
    if plan is None:
        await state.clear()
        await message.answer("Не смогла восстановить выбранный тариф. Открой каталог и выбери его заново 🙏")
        return

    try:
        promo = await services.promos.get_valid_promo(code=code, user_id=app_user.id)
        price = _plan_mode_price(plan, data.get("fulfillment_mode"))
        preview = services.promos.preview(amount=price, promo=promo)
    except DomainError as exc:
        await message.answer(
            _content(services, "purchase.promo_invalid", "Не смогла применить промокод: {reason}", reason=str(exc))
        )
        return

    promo_preview = _promo_dict(preview)
    await state.update_data(promo_code=promo.code, promo_preview=promo_preview)
    await state.set_state(PurchaseState.confirming_plan)
    await message.answer(
        _content(
            services,
            "purchase.promo_applied",
            "Промокод <b>{promo_code}</b> применён: скидка <b>{discount_amount} {currency}</b>. Итог: <b>{final_amount} {currency}</b>.",
            promo_code=promo.code,
            discount_amount=promo_preview["discount_amount"] if promo_preview else "0",
            final_amount=promo_preview["final_amount"] if promo_preview else _fmt_money(price),
            currency=plan["price_currency"],
        )
    )
    await message.answer(
        _build_plan_confirmation_text(plan, **_confirmation_kwargs_from_state(await state.get_data())),
        reply_markup=build_purchase_confirmation(plan_id, back_callback=f"buy:{plan_id}", back_text=BACK_TEXT),
    )


@router.callback_query(F.data.startswith("confirm_buy:"))
async def confirm_buy_callback(callback: CallbackQuery, state: FSMContext, app_user, services, **_: dict) -> None:
    plan_id = int(callback.data.split(":")[1])
    state_data = await state.get_data()
    preferred_server_id = state_data.get("preferred_hiddify_server_id")
    fulfillment_mode = state_data.get("fulfillment_mode")
    selected_country_name = state_data.get("selected_country_name")
    selected_server_name = state_data.get("selected_server_name")
    included_countries = state_data.get("included_countries") or []
    promo_code = state_data.get("promo_code")

    try:
        order_id, payment_url = await services.orders.create_order_with_payment(
            user_id=app_user.id,
            plan_id=plan_id,
            requested_fulfillment_mode=fulfillment_mode,
            preferred_hiddify_server_id=preferred_server_id,
            promo_code=promo_code,
        )
    except (InvalidStateError, NotFoundError, OutOfStockError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    order = await services.orders_repo.get_by_id(order_id)
    await state.clear()
    server_block = (
        f"Сервер: <b>{selected_server_name}</b>\n"
        if selected_server_name and selected_server_name != "Суперключ"
        else ""
    )
    country_block = f"Страна: <b>{selected_country_name}</b>\n" if selected_country_name else ""
    superkey_block = f"Страны суперключа: <b>{', '.join(included_countries)}</b>\n" if included_countries else ""
    amount_value = _fmt_money(order.amount_value if order else 0)
    currency = order.amount_currency if order else "RUB"
    created_text = _content(
        services,
        "purchase.created",
        (
            "🎉 <b>Готово, я создала заказ!</b>\n\n"
            "Номер заказа: <code>{order_id}</code>\n"
            "Формат: <b>{fulfillment_label}</b>\n"
            "{server_block}{country_block}{superkey_block}{promo_block}"
            "Сумма к оплате: <b>{amount_value} {currency}</b>\n\n"
            "Что делать дальше:\n"
            "1. Нажми <b>«💳 Открыть оплату»</b>.\n"
            "2. В поле сообщения к донату вставь номер заказа.\n"
            "3. После перевода я дождусь подтверждения и отправлю тебе доступ прямо сюда 💛"
        ),
        order_id=order_id,
        fulfillment_label=_fulfillment_label(fulfillment_mode or OrderFulfillmentMode.INVENTORY.value),
        server_block=server_block,
        country_block=country_block,
        superkey_block=superkey_block,
        promo_block=_promo_block_from_order(order),
        amount_value=amount_value,
        currency=currency,
    )
    await callback.message.edit_text(
        created_text,
        reply_markup=build_payment_actions(order_id=order_id, payment_url=payment_url),
    )
    await callback.answer("Ссылка на оплату уже ждёт тебя ✨")
