from __future__ import annotations

from decimal import Decimal

import pytest

from app.db.models import OrderFulfillmentMode, Payment
from app.services.exceptions import InvalidStateError
from tests.conftest import build_services, create_available_key, create_user, seed_default_plan


async def test_percent_promo_discount_is_stored_on_order_and_payment(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=801001)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://promo-percent")
    promo = await services["promos"].create_or_update(
        code="JULIA10",
        discount_type="percent",
        discount_value=Decimal("10"),
        max_uses=None,
        actor_user_id=None,
    )

    original_amount = services["pricing"].price_for_plan(plan, OrderFulfillmentMode.INVENTORY.value)
    order_id, _ = await services["orders"].create_order_with_payment(
        user_id=user.id,
        plan_id=plan.id,
        promo_code="julia10",
    )

    order = await services["orders_repo"].get_by_id(order_id)
    payment = await db.get(Payment, 1)
    refreshed_promo = await services["promo_codes_repo"].get_by_code("JULIA10")
    assert order.original_amount_value == original_amount
    assert order.discount_amount_value == Decimal("29.90")
    assert order.amount_value == Decimal("269.10")
    assert order.promo_code_id == promo.id
    assert order.promo_code == "JULIA10"
    assert payment.amount_value == order.amount_value
    assert refreshed_promo.used_count == 1


async def test_promo_cannot_be_reused_by_same_user(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=801002)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://promo-once-1")
    await create_available_key(db, services["protector"], plan.id, "vpn://promo-once-2")
    await services["promos"].create_or_update(
        code="ONCE",
        discount_type="fixed",
        discount_value=Decimal("50"),
        max_uses=None,
        actor_user_id=None,
    )
    await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id, promo_code="ONCE")

    with pytest.raises(InvalidStateError):
        await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id, promo_code="ONCE")


async def test_active_promo_applies_to_next_order_and_then_clears(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=801003)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://promo-active")
    await services["promos"].create_or_update(
        code="START",
        discount_type="fixed",
        discount_value=Decimal("100"),
        max_uses=10,
        actor_user_id=None,
    )
    await services["promos"].set_active_for_user(user_id=user.id, code="start")

    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)

    order = await services["orders_repo"].get_by_id(order_id)
    refreshed_user = await services["users_repo"].get_by_id(user.id)
    assert order.promo_code == "START"
    assert order.discount_amount_value == Decimal("100.00")
    assert refreshed_user.active_promo_code is None
